import math
import unittest

import numpy as np

from analysis import evaluate, sample_rate_hz


def synthetic_test(frequency=10, fs=150, amplitude=700, baseline_sigma=12,
                   channel1_amplitude=0, seed=1):
    rng = np.random.default_rng(seed)
    n = fs * 2
    times_base = np.arange(n) / fs
    times_exc = 3 + np.arange(n) / fs
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
        baseline, excited = synthetic_test(fs=100, frequency=25, seed=4)
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


if __name__ == '__main__':
    unittest.main()
