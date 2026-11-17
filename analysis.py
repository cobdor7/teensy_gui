"""Frequency-selective evaluation using timestamps from the serial reader."""
from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np


@dataclass(frozen=True)
class SensorResult:
    channel: int
    baseline_noise_rms: float
    response_amplitude: float
    snr_db: float
    result: str
    reason: str
    signal_min: float = 0.0
    signal_max: float = 0.0
    limits: dict | None = None


def sample_rate_hz(samples: list[tuple[float, tuple[float, ...]]]) -> float:
    if len(samples) < 2:
        return 0.0
    span = samples[-1][0] - samples[0][0]
    return (len(samples) - 1) / span if span > 0 else 0.0


def receive_timing_stats(samples):
    """Describe host arrival timing; these intervals are not device sample times."""
    if len(samples) < 2:
        raise ValueError('At least two samples are needed for receive timing')
    timestamps = np.asarray([timestamp for timestamp, _ in samples], dtype=float)
    intervals = np.diff(timestamps)
    if not np.isfinite(timestamps).all() or np.any(intervals < 0):
        raise ValueError('Invalid receive timestamps')
    return {
        'sample_count': len(samples),
        'average_receive_rate_hz': sample_rate_hz(samples),
        'median_interval_ms': float(np.median(intervals) * 1000),
        'p95_interval_ms': float(np.percentile(intervals, 95) * 1000),
        'maximum_gap_ms': float(np.max(intervals) * 1000),
        'equal_timestamp_intervals': int(np.count_nonzero(intervals == 0)),
    }


def evaluate(
    baseline: list[tuple[float, tuple[float, ...]]],
    excited: list[tuple[float, tuple[float, ...]]],
    frequency_hz: float,
    min_amplitude: float,
    min_snr_db: float,
    min_samples: int = 100,
    channel_limits: list[dict] | None = None,
) -> list[SensorResult]:
    """Fit x(t)=a*sin(2*pi*f*t)+b*cos(2*pi*f*t)+DC for each channel.

    Amplitude=sqrt(a²+b²), SNR[dB]=20*log10((amplitude/sqrt(2))/baseline_sigma).
    Uses monotonic arrival timestamps, not an assumed firmware sampling rate.
    """
    if len(baseline) < min_samples or len(excited) < min_samples:
        raise ValueError(
            f'Insufficient samples: baseline={len(baseline)}, '
            f'excited={len(excited)}; minimum={min_samples} each'
        )
    if frequency_hz <= 0 or min_amplitude < 0 or not math.isfinite(min_snr_db):
        raise ValueError('Invalid evaluation parameters')

    base = np.asarray([values for _, values in baseline], dtype=float)
    resp = np.asarray([values for _, values in excited], dtype=float)
    if base.shape[1:] != (8,) or resp.shape[1:] != (8,):
        raise ValueError('Expected eight channels per sample')
    if not np.isfinite(base).all() or not np.isfinite(resp).all():
        raise ValueError('Non-finite sensor reading')

    times = np.asarray([timestamp for timestamp, _ in excited], dtype=float)
    times = times - times[0]
    if not np.isfinite(times).all() or times[-1] <= 0:
        raise ValueError('Invalid sample timestamps')
    phase = 2.0 * np.pi * frequency_hz * times
    design = np.column_stack((np.sin(phase), np.cos(phase), np.ones(len(times))))
    if np.linalg.matrix_rank(design) != 3:
        raise ValueError('Cannot resolve requested frequency from sample timings')
    coefficients, *_ = np.linalg.lstsq(design, resp, rcond=None)
    amplitude = np.hypot(coefficients[0, :], coefficients[1, :])
    baseline_noise = np.std(base, axis=0, ddof=1)  # DC offset removed

    results = []
    for idx in range(8):
        amp = float(amplitude[idx])
        noise = float(baseline_noise[idx])
        snr = 20.0 * math.log10(max(amp / math.sqrt(2), 1e-12) / max(noise, 1e-12))
        limits = ({'min_amplitude': min_amplitude, 'min_snr_db': min_snr_db,
                   'max_baseline_noise': None, 'max_response_amplitude': None,
                   'clip_min': None, 'clip_max': None}
                  if channel_limits is None else channel_limits[idx])
        signal_min = float(min(np.min(base[:, idx]), np.min(resp[:, idx])))
        signal_max = float(max(np.max(base[:, idx]), np.max(resp[:, idx])))
        failed = []
        if amp < limits['min_amplitude']:
            failed.append('low response amplitude')
        if limits['max_response_amplitude'] is not None and amp > limits['max_response_amplitude']:
            failed.append('high response amplitude')
        if limits['max_baseline_noise'] is not None and noise > limits['max_baseline_noise']:
            failed.append('high baseline noise')
        if limits['clip_min'] is not None and signal_min <= limits['clip_min']:
            failed.append('signal at/below lower limit')
        if limits['clip_max'] is not None and signal_max >= limits['clip_max']:
            failed.append('signal at/above upper limit')
        if snr < limits['min_snr_db']:
            failed.append('low signal-to-noise ratio')
        results.append(SensorResult(
            channel=idx + 1,
            baseline_noise_rms=noise,
            response_amplitude=amp,
            snr_db=snr,
            result='FAIL' if failed else 'PASS',
            reason=', '.join(failed) if failed else 'Within configured thresholds',
            signal_min=signal_min, signal_max=signal_max, limits=limits.copy(),
        ))
    return results


def validate_capture(samples, required_samples, min_rate, max_rate, max_gap, label):
    """Reject incomplete or poorly timed captures before evaluating sensors."""
    if len(samples) < required_samples:
        raise ValueError(f'Insufficient {label} samples ({len(samples)}; need {required_samples})')
    timestamps = np.asarray([timestamp for timestamp, _ in samples], dtype=float)
    gaps = np.diff(timestamps)
    # USB/OS batching and host clock resolution can give several samples the
    # same receive timestamp. Backward time is invalid; equal time is allowed.
    if not np.isfinite(timestamps).all() or np.any(gaps < 0):
        raise ValueError(f'Invalid {label} sample timestamps')
    rate = sample_rate_hz(samples)
    if rate < min_rate or (max_rate is not None and rate > max_rate):
        raise ValueError(f'Unexpected {label} receive rate: {rate:.1f} Hz')
    largest_gap = float(np.max(gaps))
    if largest_gap > max_gap:
        raise ValueError(f'{label} timing gap {largest_gap:.3f} s exceeds {max_gap:.3f} s')
    return rate
