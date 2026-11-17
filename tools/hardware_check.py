"""Capture a baseline and one response directly from the preflashed Teensy.

This validates the wire protocol and analysis without launching the GUI. It does
not verify GUI sequencing, and it never uploads or changes device firmware.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

import serial

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analysis import evaluate, sample_rate_hz
from protocol import excitation_command, parse_sample


def capture(connection, seconds):
    samples = []
    ignored = 0
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        raw = connection.readline()
        timestamp = time.perf_counter()
        if raw:
            values = parse_sample(raw.decode('ascii', errors='replace'))
            if values is None:
                ignored += 1
            elif timestamp < end:
                samples.append((timestamp, values))
    return samples, ignored


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('port', help='Serial device, e.g. COM6 or /dev/ttyACM0')
    parser.add_argument('--frequency', type=int, default=10, help='Excitation frequency in Hz (1–25)')
    args = parser.parse_args()
    command = excitation_command(args.frequency)
    duration = 2.0
    settle = 0.5
    minimum_amplitude = 100.0
    minimum_snr = 15.0

    with serial.Serial(args.port, 115200, timeout=0.05, write_timeout=1) as connection:
        try:
            connection.reset_input_buffer()
            connection.write(b'baseline')
            connection.flush()
            time.sleep(settle)
            connection.reset_input_buffer()
            baseline, ignored_baseline = capture(connection, duration)
            connection.write(command.encode('ascii'))
            connection.flush()
            time.sleep(settle)
            connection.reset_input_buffer()
            excited, ignored_excited = capture(connection, duration)
        finally:
            connection.write(b'baseline')
            connection.flush()

    results = evaluate(baseline, excited, args.frequency, minimum_amplitude,
                       minimum_snr, min_samples=120)
    now = datetime.now(timezone.utc)
    directory = Path(__file__).resolve().parents[1] / 'runs'
    directory.mkdir(parents=True, exist_ok=True)
    stem = 'hardware_validation_' + now.strftime('%Y%m%dT%H%M%S_%fZ')
    csv_path = directory / (stem + '.csv')
    json_path = directory / (stem + '.json')
    with csv_path.open('w', newline='', encoding='utf-8') as file:
        writer = csv.writer(file)
        writer.writerow(['phase', 'relative_time_s'] + [f'sensor_{i}' for i in range(1, 9)])
        for phase, samples in (('baseline', baseline), ('excited', excited)):
            for timestamp, values in samples:
                writer.writerow([phase, f'{timestamp - baseline[0][0]:.6f}', *values])
    summary = {
        'method': 'direct serial hardware check, not GUI sequence',
        'utc_timestamp': now.isoformat(),
        'port': args.port,
        'frequency_hz': args.frequency,
        'settle_seconds': settle,
        'capture_seconds_per_phase': duration,
        'minimum_response_amplitude': minimum_amplitude,
        'minimum_snr_db': minimum_snr,
        'baseline_sample_count': len(baseline),
        'excited_sample_count': len(excited),
        'baseline_sample_rate_hz': sample_rate_hz(baseline),
        'excited_sample_rate_hz': sample_rate_hz(excited),
        'ignored_lines': {'baseline': ignored_baseline, 'excited': ignored_excited},
        'overall_result': 'PASS' if all(result.result == 'PASS' for result in results) else 'FAIL',
        'sensors': [asdict(result) for result in results],
        'raw_csv': csv_path.name,
    }
    json_path.write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(summary, indent=2))
    print(f'Saved {json_path} and {csv_path}')


if __name__ == '__main__':
    main()
