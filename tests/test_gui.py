"""Smoke-test Qt initialization, including the plugin path setup."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QColor, QImage
from main import MainWindow, FrequencyTimingsDialog


class TestGui(unittest.TestCase):
    def test_signal_limits_can_be_used_saved_and_disabled(self):
        app = QApplication.instance() or QApplication([])
        with TemporaryDirectory() as directory, patch('main.SETTINGS_PATH', Path(directory) / 'settings.json'):
            window = MainWindow()
            try:
                self.assertEqual(window.clip_min.text(), '')
                self.assertEqual(window.clip_max.text(), '')
                window.clip_min.setText('100')
                window.clip_max.setText('4000')
                window.is_connected = True
                window.worker = Mock()
                window.start_test()
                self.assertEqual(window.test_settings['clip_min'], 100)
                self.assertEqual(window.test_settings['clip_max'], 4000)
                self.assertFalse(window.clip_min.isEnabled())
                self.assertFalse(window.clip_max.isEnabled())
                window.worker = None
                window.test_state = 'IDLE'
                window.update_controls()
                window.save_settings()
                stored = json.loads((Path(directory) / 'settings.json').read_text(encoding='utf-8'))
                self.assertEqual((stored['clip_min'], stored['clip_max']), (100, 4000))

                window.clip_min.clear()
                window.save_settings()
                self.assertIsNone(json.loads((Path(directory) / 'settings.json').read_text(encoding='utf-8'))['clip_min'])
            finally:
                window.worker = None
                window.close()
                app.processEvents()
            reloaded = MainWindow()
            try:
                self.assertEqual(reloaded.clip_min.text(), '')
                self.assertEqual(float(reloaded.clip_max.text()), 4000)
            finally:
                reloaded.close()
                app.processEvents()

    def test_invalid_signal_limits_cannot_be_saved_or_used(self):
        app = QApplication.instance() or QApplication([])
        with TemporaryDirectory() as directory, patch('main.SETTINGS_PATH', Path(directory) / 'settings.json'):
            window = MainWindow()
            try:
                window.clip_min.setText('4000')
                window.clip_max.setText('100')
                with patch('main.QMessageBox.warning') as warning:
                    window.save_settings()
                warning.assert_called_once()
                self.assertFalse((Path(directory) / 'settings.json').exists())
                window.is_connected = True
                window.worker = Mock()
                window.start_test()
                self.assertEqual(window.test_state, 'ERROR')
                window.worker.send.assert_not_called()
            finally:
                window.worker = None
                window.close()
                app.processEvents()

    def test_window_starts_and_shows_unconfirmed_actuator_state(self):
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        try:
            app.processEvents()
            self.assertIn('UNKNOWN', window.actuator_label.text())
            self.assertFalse(window.run_button.isEnabled())
            self.assertEqual(window.batch_count_control.value(), 1)
            self.assertEqual(window.result_tabs.tabText(1), 'Batch comparison')
            window.all_frequencies_button.click()
            self.assertEqual([int(f.strip()) for f in window.frequency_sequence.text().split(',')],
                             list(range(1, 26)))
        finally:
            window.close()
            app.processEvents()

    def test_frequency_timing_dialog_applies_valid_overrides(self):
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        dialog = FrequencyTimingsDialog(window.settings, window)
        try:
            dialog.table.item(0, 1).setText('0.75')
            dialog.table.item(0, 2).setText('3')
            dialog.apply_timings()
            self.assertEqual(dialog.timings['1'], {'excite_settle_s': 0.75, 'excite_capture_s': 3.0})
            self.assertEqual(dialog.result(), dialog.DialogCode.Accepted)
        finally:
            dialog.close()
            window.close()
            app.processEvents()

    def test_plots_remain_readable_and_plot_data_with_batch_tab(self):
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        try:
            window.resize(1100, 650)
            window.show()
            app.processEvents()
            self.assertTrue(all(plot.height() >= 100 for plot in window.plot_widgets))
            window.plots_splitter.setSizes([180, 600])
            app.processEvents()
            self.assertGreater(window.plot_scroll.verticalScrollBar().maximum(), 0)
            window.result_tabs.setCurrentIndex(1)
            window.on_sample(1.0, tuple(float(i) for i in range(8)))
            window.on_sample(2.0, tuple(float(i + 1) for i in range(8)))
            window.render()
            _, values = window.curves[0].getData()
            self.assertEqual(values.tolist(), [0.0, 1.0])
        finally:
            window.close()
            app.processEvents()

    def test_frequency_timing_dialog_rejects_too_short_one_hz_capture(self):
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        dialog = FrequencyTimingsDialog(window.settings, window)
        try:
            dialog.table.item(0, 2).setText('1')
            with patch('main.QMessageBox.warning') as warning:
                dialog.apply_timings()
            self.assertIn('1 Hz', warning.call_args.args[2])
            self.assertNotEqual(dialog.result(), dialog.DialogCode.Accepted)
        finally:
            dialog.close()
            window.close()
            app.processEvents()

    def test_export_includes_all_eight_live_plots_even_when_scrolled(self):
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        try:
            window.resize(1100, 650)
            window.show()
            app.processEvents()
            with self.assertRaisesRegex(ValueError, 'No live sensor data'):
                window.chart_image()
            for i in range(20):
                window.on_sample(i / 10, tuple(float(i + channel) for channel in range(8)))
            window.render()
            window.plots_splitter.setSizes([180, 600])
            app.processEvents()
            self.assertGreater(window.plot_scroll.verticalScrollBar().maximum(), 0)
            with TemporaryDirectory() as directory:
                path = Path(directory) / 'live_export'
                with patch('main.QFileDialog.getSaveFileName', return_value=(str(path), 'PNG image (*.png)')):
                    window.export_chart()
                image = QImage(str(path) + '.png')
                self.assertFalse(image.isNull())
                self.assertGreaterEqual(image.width(), 900)
                self.assertGreaterEqual(image.height(), 4 * 130)
                # Both the first and last rows are rendered even when off screen.
                self.assertNotEqual(image.pixelColor(20, 50), QColor('white'))
                self.assertNotEqual(image.pixelColor(image.width() - 20, image.height() - 25), QColor('white'))
        finally:
            window.close()
            app.processEvents()

    def test_captured_chart_selection_and_export(self):
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        try:
            window.show()
            window.chart_tabs.setCurrentIndex(1)
            app.processEvents()
            with self.assertRaisesRegex(ValueError, 'No captured test charts'):
                window.chart_image()
            baseline = [(float(i), (float(i),) * 8) for i in range(3)]
            five_hz = [(10 + float(i), (float(i + 5),) * 8) for i in range(3)]
            ten_hz = [(20 + float(i), (float(i + 10),) * 8) for i in range(3)]
            window.add_captured_chart(5, baseline, five_hz)
            window.add_captured_chart(10, baseline, ten_hz)
            window.chart_frequency.setCurrentIndex(0)
            window.chart_reference.setCurrentIndex(window.chart_reference.findData('raw'))
            app.processEvents()
            _, values = window.response_curves[0].getData()
            self.assertEqual(values.tolist(), [5.0, 6.0, 7.0])
            self.assertIn('5 Hz', window.captured_label.text())
            self.assertFalse(window.chart_image().isNull())
        finally:
            window.close()
            app.processEvents()

    def test_baseline_referenced_chart_subtracts_each_channel_baseline_mean(self):
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        try:
            window.show()
            window.chart_tabs.setCurrentIndex(1)
            baseline = [(float(i), tuple(100 + 10 * ch + i * 2 for ch in range(8))) for i in range(3)]
            excited = [(10.0 + i, tuple(107 + 10 * ch + i * 3 for ch in range(8))) for i in range(3)]
            window.add_captured_chart(10, baseline, excited)
            app.processEvents()
            for channel in (0, 7):
                _, base_values = window.baseline_curves[channel].getData()
                _, response_values = window.response_curves[channel].getData()
                self.assertEqual(base_values.tolist(), [-2.0, 0.0, 2.0])
                self.assertEqual(response_values.tolist(), [5.0, 8.0, 11.0])
            self.assertIn('response − baseline mean', window.captured_label.text())
            window.chart_reference.setCurrentIndex(window.chart_reference.findData('raw'))
            _, raw_values = window.response_curves[0].getData()
            self.assertEqual(raw_values.tolist(), [107.0, 110.0, 113.0])
        finally:
            window.close()
            app.processEvents()


if __name__ == '__main__':
    unittest.main()
