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


def sample_rate_hz(samples: list[tuple[float, tuple[float, ...]]]) -> float:
    if len(samples) < 2:
        return 0.0
    span = samples[-1][0] - samples[0][0]
    return (len(samples) - 1) / span if span > 0 else 0.0


def evaluate(
    baseline: list[tuple[float, tuple[float, ...]]],
    excited: list[tuple[float, tuple[float, ...]]],
    frequency_hz: float,
    min_amplitude: float,
    min_snr_db: float,
    min_samples: int = 100,
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
        failed = []
        if amp < min_amplitude:
            failed.append('low response amplitude')
        if snr < min_snr_db:
            failed.append('low signal-to-noise ratio')
        results.append(SensorResult(
            channel=idx + 1,
            baseline_noise_rms=noise,
            response_amplitude=amp,
            snr_db=snr,
            result='FAIL' if failed else 'PASS',
            reason=', '.join(failed) if failed else 'Within configured thresholds',
        ))
    return results
