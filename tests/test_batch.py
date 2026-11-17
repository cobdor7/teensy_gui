import unittest

from batch import compare_runs


class TestBatchComparison(unittest.TestCase):
    def test_comparison_keeps_rejections_separate_from_sensor_failures(self):
        def sensor(result, amplitude, snr, noise):
            return {'channel': 1, 'result': result, 'response_amplitude': amplitude,
                    'snr_db': snr, 'baseline_noise_rms': noise}

        runs = [
            {'run_number': 1, 'frequency_results': [
                {'frequency_hz': 10, 'status': 'EVALUATED', 'sensors': [sensor('PASS', 200, 20, 5)]}]},
            {'run_number': 2, 'frequency_results': [
                {'frequency_hz': 10, 'status': 'REJECTED', 'sensors': []}]},
            {'run_number': 3, 'frequency_results': [
                {'frequency_hz': 10, 'status': 'EVALUATED', 'sensors': [sensor('FAIL', 80, 10, 8)]}]},
        ]
        result = compare_runs(runs, [10])[0]
        self.assertEqual(result['evaluated_runs'], 2)
        self.assertEqual((result['pass_count'], result['fail_count']), (1, 1))
        self.assertEqual(result['rejected_runs'], [2])
        self.assertEqual(result['response_amplitude_range'], [80, 200])
        self.assertEqual(result['snr_db_range'], [10, 20])
        self.assertEqual(result['baseline_noise_rms_range'], [5, 8])


if __name__ == '__main__':
    unittest.main()
