import math
import unittest

import numpy as np

from analysis import evaluate, sample_rate_hz, receive_timing_stats, validate_capture
from configuration import normalize_settings, resolve_limits


def synthetic_test(frequency=10, fs=150, amplitude=700, baseline_sigma=12,
                   channel1_amplitude=0, seed=1, jitter=0):
    rng = np.random.default_rng(seed)
    n = fs * 2
    times_base = np.arange(n) / fs
    times_exc = 3 + np.arange(n) / fs + rng.uniform(-jitter, jitter, n)
    offsets = np.array([-50, 125, 45, 350, 1350, 0, 15, 210], dtype=float)
    noise_base = rng.normal(0, baseline_sigma, size=(n, 8))
    noise_exc = rng.normal(0, baseline_sigma, size=(n, 8))
    amps = np.array([channel1_amplitude] + [amplitude] * 7)
    sinusoids = np.sin(2 * np.pi * frequency * (times_exc - 3))[:, None] * amps
    baseline = [(float(t), tuple(row)) for t, row in zip(times_base, offsets + noise_base)]
    excited = [(float(t), tuple(row)) for t, row in zip(times_exc, offsets + noise_exc + sinusoids)]
    return baseline, excited


class TestAnalysis(unittest.TestCase):
    def test_10hz_150_samples_per_second_correct_amplitude(self):
        baseline, excited = synthetic_test()
        results = evaluate(baseline, excited, 10, min_amplitude=100,
                           min_snr_db=15)
        self.assertEqual(results[0].result, 'FAIL')
        self.assertEqual([r.result for r in results[1:]], ['PASS'] * 7)
        self.assertAlmostEqual(results[1].response_amplitude, 700, delta=6)
        self.assertAlmostEqual(sample_rate_hz(excited), 150, delta=0.1)
        # Large DC offsets are removed by the fitted intercept.
        self.assertAlmostEqual(results[4].response_amplitude, 700, delta=6)

    def test_fit_with_sample_time_jitter(self):
        baseline, excited = synthetic_test(fs=100, frequency=25, seed=4, jitter=0.001)
        results = evaluate(baseline, excited, 25, 100, 15)
        self.assertAlmostEqual(results[1].response_amplitude, 700, delta=8)

    def test_snr_flags_noisy_channel(self):
        baseline, excited = synthetic_test(amplitude=100, baseline_sigma=50,
                                          channel1_amplitude=0, seed=2)
        results = evaluate(baseline, excited, 10, min_amplitude=50,
                           min_snr_db=15)
        self.assertEqual(results[1].result, 'FAIL')
        self.assertIn('signal-to-noise', results[1].reason)

    def test_too_few_samples_reports_error(self):
        baseline, excited = synthetic_test()
        with self.assertRaisesRegex(ValueError, 'Insufficient samples'):
            evaluate(baseline[:10], excited, 10, 100, 15)

    def test_channel_and_frequency_limits_include_noise_amplitude_and_signal_bounds(self):
        baseline, excited = synthetic_test()
        settings = normalize_settings({
            'max_baseline_noise': 100,
            'channel_criteria': {'2': {'max_baseline_noise': 5}},
            'frequency_criteria': {'10': {'channels': {'3': {'max_response_amplitude': 500},
                                                       '4': {'clip_max': 500}}}},
        })
        limits = [resolve_limits(settings, 10, i) for i in range(1, 9)]
        results = evaluate(baseline, excited, 10, 100, 15, channel_limits=limits)
        self.assertIn('high baseline noise', results[1].reason)
        self.assertIn('high response amplitude', results[2].reason)
        self.assertIn('upper limit', results[3].reason)
        self.assertEqual(results[4].result, 'PASS')
        self.assertIsNone(resolve_limits(settings, 5, 3)['max_response_amplitude'])

    def test_capture_validity_rejects_rate_gap_and_nonmonotonic_time(self):
        rows = [(i / 100, (0,) * 8) for i in range(100)]
        self.assertAlmostEqual(validate_capture(rows, 40, 60, 200, .1, 'baseline'), 100)
        with self.assertRaisesRegex(ValueError, 'receive rate'):
            validate_capture(rows, 40, 120, 200, .1, 'baseline')
        gapped = rows[:50] + [(t + .2, v) for t, v in rows[50:]]
        with self.assertRaisesRegex(ValueError, 'timing gap'):
            validate_capture(gapped, 40, 60, 200, .1, 'baseline')
        batched = rows[:50] + [rows[49]] + rows[51:]
        validate_capture(batched, 40, 60, 200, .1, 'baseline')
        nonmonotonic = rows[:50] + [rows[48]] + rows[51:]
        with self.assertRaisesRegex(ValueError, 'timestamps'):
            validate_capture(nonmonotonic, 40, 60, 200, .1, 'baseline')

    def test_receive_timing_reports_average_and_arrival_gaps(self):
        samples = [(t, (0,) * 8) for t in (0, 0.01, 0.01, 0.02, 0.05)]
        stats = receive_timing_stats(samples)
        self.assertEqual(stats['sample_count'], 5)
        self.assertAlmostEqual(stats['average_receive_rate_hz'], 80)
        self.assertAlmostEqual(stats['median_interval_ms'], 10)
        self.assertAlmostEqual(stats['maximum_gap_ms'], 30)
        self.assertEqual(stats['equal_timestamp_intervals'], 1)
        with self.assertRaises(ValueError):
            receive_timing_stats([(2.0, ()), (1.0, ())])

    def test_invalid_criteria_are_rejected_instead_of_silently_disabled(self):
        with self.assertRaises(ValueError):
            normalize_settings({'channel_criteria': {'4': {'max_baseline_noise': -1}}})
        with self.assertRaises(ValueError):
            normalize_settings({'channel_criteria': {'1': {'clip_min': 20, 'clip_max': 10}}})


if __name__ == '__main__':
    unittest.main()
