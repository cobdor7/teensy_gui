"""Exercise the real sequencing and logging methods without a GUI event loop."""
from collections import deque
import csv
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import main


class WindowStub:
    ACTIVE_STATES = main.MainWindow.ACTIVE_STATES
    start_test = main.MainWindow.start_test
    on_command_sent = main.MainWindow.on_command_sent
    on_sample = main.MainWindow.on_sample
    advance_test = main.MainWindow.advance_test
    required_samples = main.MainWindow.required_samples
    begin_capture = main.MainWindow.begin_capture
    check_capture = main.MainWindow.check_capture
    request_response = main.MainWindow.request_response
    record_rejected_frequency = main.MainWindow.record_rejected_frequency
    finish_sequence = main.MainWindow.finish_sequence
    update_batch_view = main.MainWindow.update_batch_view
    write_batch_summary = main.MainWindow.write_batch_summary
    record_interrupted_batch = main.MainWindow.record_interrupted_batch
    on_malformed = main.MainWindow.on_malformed
    on_device_message = main.MainWindow.on_device_message
    complete_test = main.MainWindow.complete_test
    finish_test_error = main.MainWindow.finish_test_error
    abort_test = main.MainWindow.abort_test
    log_results = main.MainWindow.log_results

    def __init__(self):
        self.settings = dict(main.DEFAULT_SETTINGS, baseline_settle_s=0.1,
                             excite_settle_s=0.1, baseline_capture_s=0.5,
                             excite_capture_s=0.5)
        self.is_connected = True
        self.config_error = ''
        self.worker = Mock()
        self.batch_count = 1
        self.batch_count_control = Mock(value=Mock(return_value=1))
        self.batch_runs = []
        self.frequency = Mock(value=Mock(return_value=10))
        self.frequency_sequence = Mock(text=Mock(return_value=''))
        self.min_amplitude = Mock(value=Mock(return_value=100))
        self.min_snr = Mock(value=Mock(return_value=15))
        self.port = Mock(currentData=Mock(return_value='FAKE'))
        self.test_state = 'IDLE'
        self.history = [deque(maxlen=1000) for _ in range(8)]
        self.connection_label = Mock()
        self.actuator_label = Mock()
        self.rate_label = Mock()
        self.results = Mock()
        self.results.rowCount.return_value = 0
        self.batch_results = Mock()
        self.batch_results.rowCount.return_value = 0
        self.batch_comparison = Mock()
        self.batch_comparison.rowCount.return_value = 0
        self.result_tabs = Mock()
        self.reset_results = Mock()
        self.clear_captured_charts = Mock()
        self.add_captured_chart = Mock()
        self.state_history = []

    def set_test_state(self, state, suffix=''):
        self.test_state = state
        self.state_history.append((state, suffix))


class TestSequence(unittest.TestCase):
    def setUp(self):
        self.window = WindowStub()

    def tick(self, timestamp):
        with patch('main.time.perf_counter', return_value=timestamp):
            self.window.advance_test()

    def begin(self):
        with patch('main.time.perf_counter', return_value=100.0):
            self.window.start_test()
        self.window.on_command_sent('baseline', 100.0)
        self.tick(100.11)

    def test_complete_sequence_and_output_files(self):
        w = self.window
        self.begin()
        self.assertEqual(w.test_state, 'BASELINE_CAPTURE')
        for i in range(50):
            w.on_sample(100.11 + i / 100, (float((-1) ** i),) * 8)
        self.tick(100.62)
        self.assertEqual(w.test_state, 'WAIT_RESPONSE_COMMAND')
        w.on_command_sent('response:10', 100.62)
        self.tick(100.73)
        for i in range(50):
            t = 100.73 + i / 100
            signal = 500 * math.sin(2 * math.pi * 10 * (t - 100.73))
            w.on_sample(t, (0.0,) + (signal,) * 7)
        with TemporaryDirectory() as directory, patch('main.ROOT', Path(directory)), \
                patch('main.QTableWidgetItem', return_value=Mock()), patch('main.QColor'):
            self.tick(101.24)
            self.assertEqual(w.test_state, 'FAIL')
            self.assertEqual([result.result for result in w.last_results], ['FAIL'] + ['PASS'] * 7)
            summary_files = list((Path(directory) / 'runs').glob('*.json'))
            self.assertEqual(len(summary_files), 1)
            summary = json.loads(summary_files[0].read_text(encoding='utf-8'))
            self.assertEqual(summary['overall_result'], 'FAIL')
            self.assertEqual(summary['frequencies_hz'], [10])
            self.assertEqual(summary['baseline_sample_count'], 50)
            self.assertEqual(summary['baseline_capture_s'], 0.5)
            self.assertEqual(summary['receive_clock'], 'time.perf_counter')
            self.assertAlmostEqual(summary['baseline_receive_timing']['average_receive_rate_hz'], 100)
            self.assertEqual(len(summary['frequency_results'][0]['sensors']), 8)
            self.assertAlmostEqual(summary['frequency_results'][0]['receive_timing']['maximum_gap_ms'], 10)
            with (summary_files[0].parent / summary['raw_csv']).open(newline='') as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(len(rows), 100)
            self.assertEqual({row['phase'] for row in rows}, {'baseline', 'excited'})
        self.assertEqual([call.args[0] for call in w.worker.send.call_args_list],
                         ['baseline', 'response:10', 'baseline'])

    def test_abort_requests_baseline_and_does_not_finish(self):
        w = self.window
        self.begin()
        w.abort_test()
        self.assertEqual(w.test_state, 'ABORTED')
        self.assertEqual(w.worker.send.call_args_list[-1].args[0], 'baseline')
        self.tick(102.0)
        self.assertEqual(w.test_state, 'ABORTED')

    def test_insufficient_baseline_requests_baseline(self):
        w = self.window
        self.begin()
        self.tick(100.62)
        self.assertEqual(w.test_state, 'ERROR')
        self.assertIn('Insufficient baseline', w.state_history[-1][1])
        self.assertEqual(w.worker.send.call_args_list[-1].args[0], 'baseline')

    def test_capture_stall_at_window_end_is_test_error(self):
        w = self.window
        self.begin()
        for i in range(40):
            w.on_sample(100.11 + i / 100, (0.0,) * 8)
        self.tick(100.62)
        self.assertEqual(w.test_state, 'ERROR')
        self.assertIn('capture boundary', w.state_history[-1][1])

    def test_malformed_capture_is_test_error(self):
        w = self.window
        self.begin()
        w.on_malformed(100.2)
        self.assertEqual(w.test_state, 'ERROR')
        self.assertIn('Malformed', w.state_history[-1][1])
        self.assertEqual(w.worker.send.call_args_list[-1].args[0], 'baseline')

    def test_mismatched_ack_is_test_error(self):
        w = WindowStub()
        w.test_hz = 20
        w.test_state = 'EXCITE_SETTLE'
        w.on_device_message(1.0, '15.00')
        self.assertEqual(w.test_state, 'ERROR')
        self.assertEqual(w.worker.send.call_args_list[-1].args[0], 'baseline')

    def test_rejected_frequencies_are_recorded_and_sequence_continues(self):
        w = self.window
        w.frequency_sequence.text.return_value = '10, 20, 21'
        self.begin()
        for i in range(50):
            w.on_sample(100.11 + i / 100, (float((-1) ** i),) * 8)
        self.tick(100.62)
        w.on_command_sent('response:10', 100.62)
        self.tick(100.73)
        for i in range(50):
            t = 100.73 + i / 100
            w.on_sample(t, (500 * math.sin(2 * math.pi * 10 * (t - 100.73)),) * 8)
        with TemporaryDirectory() as directory, patch('main.ROOT', Path(directory)), \
                patch('main.QTableWidgetItem', return_value=Mock()), patch('main.QColor'):
            self.tick(101.24)
            w.on_command_sent('response:20', 101.24)
            with patch('main.time.perf_counter', return_value=101.25):
                w.on_device_message(101.25, 'response frequency invalid')
            self.assertEqual(w.test_state, 'WAIT_REJECTED')
            w.on_device_message(101.26, '10.00')  # stale acknowledgement ignored
            self.tick(101.56)
            self.assertEqual(w.worker.send.call_args_list[-1].args[0], 'response:21')
            w.on_command_sent('response:21', 101.56)
            with patch('main.time.perf_counter', return_value=101.57):
                w.on_device_message(101.57, 'response frequency invalid')
            self.tick(101.88)
            self.assertEqual(w.test_state, 'ERROR')
            summary = json.loads(next((Path(directory) / 'runs').glob('*.json')).read_text(encoding='utf-8'))
            self.assertEqual(summary['overall_result'], 'ERROR')
            self.assertEqual([r['status'] for r in summary['frequency_results']],
                             ['EVALUATED', 'REJECTED', 'REJECTED'])
            self.assertEqual([r['frequency_hz'] for r in summary['frequency_results']], [10, 20, 21])
            self.assertEqual(summary['frequency_results'][1]['sensors'], [])
            self.assertIn('20 Hz', w.state_history[-1][1])
            self.assertIn('21 Hz', w.state_history[-1][1])

    def test_full_frequency_list_starts_with_one_hz(self):
        w = self.window
        w.settings['excite_capture_s'] = 2.0
        w.frequency_sequence.text.return_value = ', '.join(str(f) for f in range(1, 26))
        with patch('main.time.perf_counter', return_value=100.0):
            w.start_test()
        self.assertEqual(w.test_frequencies, list(range(1, 26)))
        self.assertEqual(w.test_hz, 1)
        self.assertEqual(w.test_state, 'WAIT_BASELINE_COMMAND')

    def test_each_frequency_uses_and_logs_its_own_timing(self):
        w = self.window
        w.frequency_sequence.text.return_value = '10, 20'
        w.settings['frequency_timings'] = {
            '10': {'excite_settle_s': 0.2, 'excite_capture_s': 0.6},
            '20': {'excite_settle_s': 0.3, 'excite_capture_s': 0.7},
        }
        self.begin()
        for i in range(50):
            w.on_sample(100.11 + i / 100, (float((-1) ** i),) * 8)
        self.tick(100.62)
        w.on_command_sent('response:10', 100.62)
        self.assertAlmostEqual(w.deadline, 100.82)
        self.tick(100.83)
        self.assertAlmostEqual(w.deadline, 101.43)
        for i in range(60):
            t = 100.83 + i / 100
            w.on_sample(t, (500 * math.sin(2 * math.pi * 10 * (t - 100.83)),) * 8)
        with TemporaryDirectory() as directory, patch('main.ROOT', Path(directory)), \
                patch('main.QTableWidgetItem', return_value=Mock()), patch('main.QColor'):
            self.tick(101.44)
            w.on_command_sent('response:20', 101.44)
            self.assertAlmostEqual(w.deadline, 101.74)
            self.tick(101.75)
            self.assertAlmostEqual(w.deadline, 102.45)
            for i in range(70):
                t = 101.75 + i / 100
                w.on_sample(t, (500 * math.sin(2 * math.pi * 20 * (t - 101.75)),) * 8)
            self.tick(102.46)
            self.assertEqual(w.test_state, 'PASS')
            summary = json.loads(next((Path(directory) / 'runs').glob('*.json')).read_text(encoding='utf-8'))
            self.assertEqual([(r['excite_settle_s'], r['excite_capture_s']) for r in summary['frequency_results']],
                             [(0.2, 0.6), (0.3, 0.7)])

    def test_short_override_reports_frequency_before_running(self):
        w = self.window
        w.settings['frequency_timings'] = {'1': {'excite_capture_s': 0.5}}
        w.frequency_sequence.text.return_value = '1, 10'
        w.start_test()
        self.assertEqual(w.test_state, 'ERROR')
        self.assertIn('1 Hz capture', w.state_history[-1][1])
        w.worker.send.assert_not_called()

    def test_two_run_batch_saves_individual_runs_and_comparison(self):
        w = self.window
        w.batch_count_control.value.return_value = 2
        w.frequency_sequence.text.return_value = '10'
        with TemporaryDirectory() as directory, patch('main.ROOT', Path(directory)), \
                patch('main.QTableWidgetItem', return_value=Mock()), patch('main.QColor'):
            self.begin()
            for i in range(50):
                w.on_sample(100.11 + i / 100, (float((-1) ** i),) * 8)
            self.tick(100.62)
            w.on_command_sent('response:10', 100.62)
            self.tick(100.73)
            for i in range(50):
                t = 100.73 + i / 100
                signal = 500 * math.sin(2 * math.pi * 10 * (t - 100.73))
                w.on_sample(t, (0.0,) + (signal,) * 7)
            self.tick(101.24)
            self.assertEqual(w.test_state, 'WAIT_BASELINE_COMMAND')
            self.assertEqual(w.batch_run_number, 2)
            summary_path = next((Path(directory) / 'runs').glob('batch_*.json'))
            intermediate = json.loads(summary_path.read_text(encoding='utf-8'))
            self.assertEqual((intermediate['status'], intermediate['completed_runs']), ('IN_PROGRESS', 1))
            w.on_command_sent('baseline', 101.25)
            self.tick(101.36)
            for i in range(50):
                w.on_sample(101.36 + i / 100, (float((-1) ** i),) * 8)
            self.tick(101.87)
            w.on_command_sent('response:10', 101.87)
            self.tick(101.98)
            for i in range(50):
                t = 101.98 + i / 100
                signal = 300 * math.sin(2 * math.pi * 10 * (t - 101.98))
                w.on_sample(t, (signal,) * 8)
            self.tick(102.49)
            self.assertEqual(w.test_state, 'FAIL')
            summary = json.loads(summary_path.read_text(encoding='utf-8'))
            self.assertEqual((summary['status'], summary['overall_result'], summary['completed_runs']),
                             ('COMPLETE', 'FAIL', 2))
            self.assertEqual([run['overall_result'] for run in summary['runs']], ['FAIL', 'PASS'])
            self.assertEqual(len(list((Path(directory) / 'runs').glob('test_*.json'))), 2)
            sensor1 = summary['comparison'][0]
            self.assertEqual((sensor1['pass_count'], sensor1['fail_count']), (1, 1))
            self.assertAlmostEqual(sensor1['response_amplitude_range'][1], 300, delta=0.01)
            self.assertEqual([call.args[0] for call in w.worker.send.call_args_list],
                             ['baseline', 'response:10', 'baseline', 'response:10', 'baseline'])

    def test_aborted_batch_records_incomplete_status(self):
        w = self.window
        w.batch_count_control.value.return_value = 3
        with TemporaryDirectory() as directory, patch('main.ROOT', Path(directory)):
            self.begin()
            w.abort_test()
            self.assertEqual(w.test_state, 'ABORTED')
            summary = json.loads(next((Path(directory) / 'runs').glob('batch_*.json')).read_text(encoding='utf-8'))
            self.assertEqual((summary['status'], summary['completed_runs'], summary['interrupted_run']),
                             ('ABORTED', 0, 1))
            self.assertIsNone(summary['overall_result'])

    def test_aborting_second_run_preserves_completed_batch_run(self):
        w = self.window
        w.batch_count_control.value.return_value = 2
        with TemporaryDirectory() as directory, patch('main.ROOT', Path(directory)), \
                patch('main.QTableWidgetItem', return_value=Mock()), patch('main.QColor'):
            self.begin()
            for i in range(50):
                w.on_sample(100.11 + i / 100, (float((-1) ** i),) * 8)
            self.tick(100.62)
            w.on_command_sent('response:10', 100.62)
            self.tick(100.73)
            for i in range(50):
                t = 100.73 + i / 100
                signal = 500 * math.sin(2 * math.pi * 10 * (t - 100.73))
                w.on_sample(t, (signal,) * 8)
            self.tick(101.24)
            self.assertEqual(w.batch_run_number, 2)
            w.abort_test()
            summary = json.loads(next((Path(directory) / 'runs').glob('batch_*.json')).read_text(encoding='utf-8'))
            self.assertEqual((summary['status'], summary['completed_runs'], summary['interrupted_run']),
                             ('ABORTED', 1, 2))
            self.assertEqual(summary['runs'][0]['overall_result'], 'PASS')
            self.assertEqual(len(list((Path(directory) / 'runs').glob('test_*.json'))), 1)
            self.assertEqual(w.worker.send.call_args_list[-1].args[0], 'baseline')

    def test_batch_continues_after_rejection_and_marks_overall_error(self):
        w = self.window
        w.batch_count_control.value.return_value = 2
        w.frequency_sequence.text.return_value = '20'
        with TemporaryDirectory() as directory, patch('main.ROOT', Path(directory)), \
                patch('main.QTableWidgetItem', return_value=Mock()), patch('main.QColor'):
            self.begin()
            for i in range(50):
                w.on_sample(100.11 + i / 100, (0.0,) * 8)
            self.tick(100.62)
            w.on_command_sent('response:20', 100.62)
            with patch('main.time.perf_counter', return_value=100.63):
                w.on_device_message(100.63, 'response frequency invalid')
            self.tick(100.94)
            self.assertEqual(w.test_state, 'WAIT_BASELINE_COMMAND')
            self.assertEqual(w.batch_run_number, 2)
            w.on_command_sent('baseline', 100.95)
            self.tick(101.06)
            for i in range(50):
                w.on_sample(101.06 + i / 100, (0.0,) * 8)
            self.tick(101.57)
            w.on_command_sent('response:20', 101.57)
            with patch('main.time.perf_counter', return_value=101.58):
                w.on_device_message(101.58, 'response frequency invalid')
            self.tick(101.89)
            self.assertEqual(w.test_state, 'ERROR')
            summary = json.loads(next((Path(directory) / 'runs').glob('batch_*.json')).read_text(encoding='utf-8'))
            self.assertEqual((summary['status'], summary['overall_result'], summary['completed_runs']),
                             ('COMPLETE', 'ERROR', 2))
            self.assertEqual(summary['comparison'][0]['rejected_runs'], [1, 2])
            self.assertEqual(summary['comparison'][0]['fail_count'], 0)
            self.assertIsNone(summary['comparison'][0]['response_amplitude_range'])

    def test_unexpected_text_during_capture_is_test_error(self):
        w = self.window
        self.begin()
        w.on_device_message(100.2, 'garbled sensor row')
        self.assertEqual(w.test_state, 'ERROR')
        self.assertIn('Unexpected text', w.state_history[-1][1])

    def test_frequency_sequence_collects_and_logs_each_frequency(self):
        w = self.window
        w.frequency_sequence.text.return_value = '10, 20'
        self.begin()
        for i in range(50):
            w.on_sample(100.11 + i / 100, (float((-1) ** i),) * 8)
        self.tick(100.62)
        w.on_command_sent('response:10', 100.62)
        self.tick(100.73)
        for i in range(50):
            t = 100.73 + i / 100
            w.on_sample(t, (500 * math.sin(2 * math.pi * 10 * (t - 100.73)),) * 8)
        with TemporaryDirectory() as directory, patch('main.ROOT', Path(directory)), \
                patch('main.QTableWidgetItem', return_value=Mock()), patch('main.QColor'):
            self.tick(101.24)
            self.assertEqual(w.test_state, 'WAIT_RESPONSE_COMMAND')
            self.assertEqual(w.worker.send.call_args_list[-1].args[0], 'response:20')
            w.on_command_sent('response:20', 101.24)
            self.tick(101.35)
            for i in range(50):
                t = 101.35 + i / 100
                w.on_sample(t, (500 * math.sin(2 * math.pi * 20 * (t - 101.35)),) * 8)
            self.tick(101.86)
            self.assertEqual(w.test_state, 'PASS')
            summary_path = next((Path(directory) / 'runs').glob('*.json'))
            summary = json.loads(summary_path.read_text(encoding='utf-8'))
            self.assertEqual(summary['frequencies_hz'], [10, 20])
            self.assertEqual([run['frequency_hz'] for run in summary['frequency_results']], [10, 20])
            with (summary_path.parent / summary['raw_csv']).open(newline='') as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(len(rows), 150)
            self.assertEqual({row['frequency_hz'] for row in rows if row['phase'] == 'excited'}, {'10', '20'})
        self.assertEqual(w.worker.send.call_args_list[-1].args[0], 'baseline')


class TestSettings(unittest.TestCase):
    def test_external_criteria_are_loaded_and_used_for_sensor_results(self):
        with TemporaryDirectory() as directory, patch('main.SETTINGS_PATH', Path(directory) / 'settings.json'):
            main.SETTINGS_PATH.write_text(json.dumps({
                'min_amplitude': 200,
                'channel_criteria': {'1': {'min_amplitude': 800}},
            }), encoding='utf-8')
            settings = main.load_settings()
            limits = [main.resolve_limits(settings, 10, channel) for channel in range(1, 9)]
            baseline = [(i / 100, (0.0,) * 8) for i in range(100)]
            excited = [(i / 100, (500 * math.sin(2 * math.pi * 10 * i / 100),) * 8)
                       for i in range(100)]
            results = main.evaluate(baseline, excited, 10, settings['min_amplitude'],
                                    settings['min_snr_db'], channel_limits=limits)
            self.assertEqual(results[0].result, 'FAIL')
            self.assertEqual(results[1].result, 'PASS')
            self.assertEqual(results[0].limits['min_amplitude'], 800)
            self.assertEqual(results[1].limits['min_amplitude'], 200)

    def test_numeric_strings_are_normalized_and_nonfinite_values_rejected(self):
        with TemporaryDirectory() as directory, patch('main.SETTINGS_PATH', Path(directory) / 'settings.json'):
            main.SETTINGS_PATH.write_text('{"baseline_capture_s": "2.5"}', encoding='utf-8')
            self.assertEqual(main.load_settings()['baseline_capture_s'], 2.5)
            main.SETTINGS_PATH.write_text('{"min_snr_db": NaN}', encoding='utf-8')
            self.assertIn('_config_error', main.load_settings())


if __name__ == '__main__':
    unittest.main()
