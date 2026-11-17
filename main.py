"""Eight-channel sensor test station. Only ASCII commands are sent to Teensy."""
from __future__ import annotations

from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
import csv
import json
import math
import os
from pathlib import Path
import queue
import sys
import threading
import time

import PySide6

# Some PySide6 releases misread their embedded Qt configuration when an install
# path contains a comma. Set the actual bundled plugin directories explicitly.
_qt_plugins = Path(PySide6.__file__).resolve().parent / 'plugins'
if sys.platform == 'win32' and _qt_plugins.is_dir():
    os.environ.setdefault('QT_PLUGIN_PATH', str(_qt_plugins))
    os.environ.setdefault('QT_QPA_PLATFORM_PLUGIN_PATH', str(_qt_plugins / 'platforms'))

import pyqtgraph as pg
import serial
from serial.tools import list_ports
from PySide6.QtCore import QRect, QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton, QScrollArea, QSpinBox, QSplitter, QTabWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget, QHeaderView,
)

from analysis import evaluate, sample_rate_hz, receive_timing_stats, validate_capture
from batch import compare_runs
from configuration import DEFAULT_SETTINGS, normalize_settings, resolve_limits, resolve_timing
from protocol import excitation_command, parse_sample


ROOT = Path(sys.executable if getattr(sys, 'frozen', False) else __file__).resolve().parent
SETTINGS_PATH = ROOT / 'settings.json'
def load_settings() -> dict:
    if not SETTINGS_PATH.exists():
        return deepcopy(DEFAULT_SETTINGS)
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding='utf-8'))
        return normalize_settings(data)
    except (OSError, ValueError, TypeError, OverflowError) as exc:
        settings = deepcopy(DEFAULT_SETTINGS)
        settings['_config_error'] = str(exc)
        return settings


class FrequencyTimingsDialog(QDialog):
    """Edit optional per-frequency settling and capture durations."""
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Frequency timing')
        self.resize(560, 650)
        self.settings = deepcopy(settings)
        self.timings = deepcopy(settings['frequency_timings'])
        layout = QVBoxLayout(self)
        info = QLabel(f'Leave a cell blank to use the default: settle '
                      f'{settings["excite_settle_s"]:g} s, capture {settings["excite_capture_s"]:g} s.\n'
                      'Each selected frequency needs at least two cycles of capture.')
        info.setWordWrap(True)
        layout.addWidget(info)
        self.table = QTableWidget(25, 3)
        self.table.setHorizontalHeaderLabels(['Frequency', 'Settle override (s)', 'Capture override (s)'])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for row in range(25):
            frequency = row + 1
            label = QTableWidgetItem(f'{frequency} Hz')
            label.setFlags(label.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 0, label)
            overrides = self.timings.get(str(frequency), {})
            for col, key in ((1, 'excite_settle_s'), (2, 'excite_capture_s')):
                value = overrides.get(key)
                self.table.setItem(row, col, QTableWidgetItem('' if value is None else f'{value:g}'))
        layout.addWidget(self.table)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.apply_timings)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def apply_timings(self):
        overrides = {}
        for row in range(25):
            fields = {}
            for col, key in ((1, 'excite_settle_s'), (2, 'excite_capture_s')):
                text = self.table.item(row, col).text().strip()
                if text:
                    fields[key] = text
            if fields:
                overrides[str(row + 1)] = fields
        candidate = deepcopy(self.settings)
        candidate['frequency_timings'] = overrides
        try:
            candidate = normalize_settings(candidate)
            for frequency in overrides:
                capture = resolve_timing(candidate, int(frequency))['excite_capture_s']
                if capture * int(frequency) < 2:
                    raise ValueError(f'{frequency} Hz capture must cover at least two cycles')
        except ValueError as exc:
            QMessageBox.warning(self, 'Invalid frequency timing', str(exc))
            return
        self.timings = candidate['frequency_timings']
        self.accept()


class SerialWorker(QThread):
    sample = Signal(float, object)         # monotonic arrival timestamp, 8 floats
    connected = Signal(bool)
    status = Signal(str)
    command_sent = Signal(str, float)      # actual write completed
    malformed = Signal(float)             # malformed CSV-like sensor row
    device_message = Signal(float, str)    # text/optional frequency acknowledgement

    def __init__(self, port: str):
        super().__init__()
        self.port = port
        self._stopped = threading.Event()
        self._outgoing: queue.Queue[str] = queue.Queue()

    def send(self, command: str):
        """Queue, rather than write from the GUI thread (thread-safe)."""
        self._outgoing.put(command)

    def run(self):
        try:
            with serial.Serial(self.port, 115200, timeout=0.05, write_timeout=1) as conn:
                conn.reset_input_buffer()  # drop stale pre-connect data
                self.connected.emit(True)
                self.status.emit(f'Connected: {self.port}')
                while not self._stopped.is_set():
                    try:
                        while True:
                            command = self._outgoing.get_nowait()
                            # Firmware expects raw ASCII with NO terminating newline.
                            conn.write(command.encode('ascii'))
                            conn.flush()
                            now = time.perf_counter()
                            self.command_sent.emit(command, now)
                    except queue.Empty:
                        pass

                    raw = conn.readline()
                    if not raw:
                        continue
                    timestamp = time.perf_counter()
                    values = parse_sample(raw.decode('ascii', errors='replace'))
                    if values is not None:
                        self.sample.emit(timestamp, values)
                    elif b',' in raw:
                        self.malformed.emit(timestamp)
                    else:
                        message = raw.decode('ascii', errors='replace').strip()
                        if message:
                            self.device_message.emit(timestamp, message)
                # A disconnect should request actuator OFF before closing USB.
                try:
                    conn.write(b'baseline')
                    conn.flush()
                except (serial.SerialException, OSError):
                    pass
        except (serial.SerialException, OSError) as exc:
            self.status.emit(f'Serial error: {exc}')
        finally:
            self.connected.emit(False)

    def stop(self):
        self._stopped.set()
        self.wait(2000)


class MainWindow(QMainWindow):
    ACTIVE_STATES = {
        'WAIT_BASELINE_COMMAND', 'BASELINE_SETTLE', 'BASELINE_CAPTURE',
        'WAIT_RESPONSE_COMMAND', 'EXCITE_SETTLE', 'EXCITE_CAPTURE', 'WAIT_REJECTED',
    }

    def __init__(self):
        super().__init__()
        self.settings = load_settings()
        self.config_error = self.settings.pop('_config_error', '')
        self.setWindowTitle('Sensor Test Station — Automated Evaluation')
        screen = QApplication.primaryScreen()
        available = screen.availableGeometry() if screen else None
        self.resize(min(1250, available.width()) if available else 1250,
                    min(1020, available.height()) if available else 1020)
        self.worker: SerialWorker | None = None
        self.is_connected = False
        self.history = [deque(maxlen=1000) for _ in range(8)]
        self.baseline_samples = []
        self.excited_samples = []
        self.test_state = 'IDLE'
        self.deadline = 0.0
        self.capture_start = 0.0
        self.expected_command = ''
        self.command_wait_start = 0.0
        self.test_hz = 10
        self.test_min_amplitude = 100.0
        self.test_min_snr_db = 15.0
        self.last_command = 'None'
        self.last_results = None
        self.frequency_runs = []
        self.batch_count = 1
        self.batch_runs = []
        self.chart_captures = {}

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        ports = QHBoxLayout()
        self.port = QComboBox()
        self.refresh_ports()
        self.refresh_button = QPushButton('Refresh Ports')
        self.refresh_button.clicked.connect(self.refresh_ports)
        self.connect_button = QPushButton('Connect')
        self.connect_button.clicked.connect(self.toggle_connection)
        for w in (QLabel('Port:'), self.port, self.refresh_button, self.connect_button):
            ports.addWidget(w)
        ports.addStretch()
        layout.addLayout(ports)

        controls = QHBoxLayout()
        self.frequency = QDoubleSpinBox()
        self.frequency.setDecimals(0)
        self.frequency.setRange(1, 25)
        self.frequency.setValue(self.settings['default_frequency_hz'])
        self.frequency.setSuffix(' Hz')
        self.frequency_sequence = QLineEdit(', '.join(str(f) for f in self.settings['test_frequencies_hz']))
        self.frequency_sequence.setPlaceholderText('e.g. 5, 10, 20; blank uses selected frequency')
        self.all_frequencies_button = QPushButton('Use all frequencies (1–25 Hz)')
        self.all_frequencies_button.setToolTip('Fill the sequence with every integer frequency; then run the test.')
        self.all_frequencies_button.clicked.connect(self.select_all_frequencies)
        self.timing_button = QPushButton('Frequency timing…')
        self.timing_button.setToolTip('Set optional settling and capture times for each frequency.')
        self.timing_button.clicked.connect(self.edit_frequency_timings)
        self.baseline_button = QPushButton('Baseline / OFF')
        self.baseline_button.clicked.connect(self.baseline)
        self.response_button = QPushButton('Excite / ON')
        self.response_button.clicked.connect(self.response)
        self.run_button = QPushButton('RUN AUTOMATIC TEST')
        self.run_button.clicked.connect(self.start_test)
        self.abort_button = QPushButton('Abort Test')
        self.abort_button.clicked.connect(self.abort_test)
        self.batch_count_control = QSpinBox()
        self.batch_count_control.setRange(1, 100)
        self.batch_count_control.setValue(1)
        self.batch_count_control.setToolTip('Repeat the selected frequency sequence with a fresh baseline for each run.')
        for w in (QLabel('Frequency:'), self.frequency, self.baseline_button,
                  self.response_button, QLabel('Runs:'), self.batch_count_control,
                  self.run_button, self.abort_button):
            controls.addWidget(w)
        layout.addLayout(controls)
        sequence_line = QHBoxLayout()
        sequence_line.addWidget(QLabel('Test frequencies (Hz):'))
        sequence_line.addWidget(self.frequency_sequence)
        sequence_line.addWidget(self.all_frequencies_button)
        sequence_line.addWidget(self.timing_button)
        layout.addLayout(sequence_line)

        thresholds = QHBoxLayout()
        self.min_amplitude = QDoubleSpinBox()
        self.min_amplitude.setRange(0, 1_000_000)
        self.min_amplitude.setDecimals(1)
        self.min_amplitude.setValue(float(self.settings['min_amplitude']))
        self.min_snr = QDoubleSpinBox()
        self.min_snr.setRange(-50, 100)
        self.min_snr.setDecimals(1)
        self.min_snr.setSuffix(' dB')
        self.min_snr.setValue(float(self.settings['min_snr_db']))
        self.save_button = QPushButton('Save Settings')
        self.save_button.clicked.connect(self.save_settings)
        for w in (QLabel('Minimum response amplitude:'), self.min_amplitude,
                  QLabel('Minimum SNR:'), self.min_snr, self.save_button):
            thresholds.addWidget(w)
        thresholds.addStretch()
        layout.addLayout(thresholds)

        self.connection_label = QLabel('Disconnected | Last command: None')
        self.actuator_label = QLabel('Actuator requested: UNKNOWN (no hardware feedback)')
        self.test_label = QLabel('TEST ERROR: invalid settings — ' + self.config_error if self.config_error else 'Test: IDLE')
        self.test_label.setWordWrap(True)
        self.rate_label = QLabel('Host receive rate and gaps: —')
        layout.addWidget(self.connection_label)
        layout.addWidget(self.actuator_label)
        layout.addWidget(self.test_label)
        layout.addWidget(self.rate_label)

        chart_controls = QHBoxLayout()
        chart_controls.addWidget(QLabel('Charts:'))
        chart_controls.addStretch()
        self.export_chart_button = QPushButton('Export current charts to PNG…')
        self.export_chart_button.clicked.connect(self.export_chart)
        chart_controls.addWidget(self.export_chart_button)
        layout.addLayout(chart_controls)

        plot_panel = QWidget()
        plots = QGridLayout(plot_panel)
        plots.setContentsMargins(0, 0, 0, 0)
        self.curves = []
        self.plot_widgets = []
        for idx in range(8):
            graph = pg.PlotWidget(title=f'Sensor {idx + 1}')
            graph.setMinimumHeight(100)
            graph.setLabel('bottom', 'Recent time', units='s')
            graph.setXRange(-5, 0, padding=0)
            graph.setMouseEnabled(x=False, y=True)
            graph.showGrid(x=True, y=True, alpha=0.25)
            curve = graph.plot(pen=pg.mkPen(pg.intColor(idx, hues=8), width=1.8))
            self.curves.append(curve)
            self.plot_widgets.append(graph)
            plots.addWidget(graph, idx // 2, idx % 2)

        self.results = QTableWidget(0, 8)
        self.results.setHorizontalHeaderLabels(
            ['Hz', 'Sensor', 'Noise / max', 'Amplitude / min–max', 'SNR / min', 'Range / limits', 'Result', 'Reason']
        )
        self.results.verticalHeader().setVisible(False)
        self.results.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.results.setMinimumHeight(0)
        self.result_tabs = QTabWidget()
        self.result_tabs.addTab(self.results, 'Current run')
        batch_view = QWidget()
        batch_layout = QVBoxLayout(batch_view)
        batch_layout.addWidget(QLabel('Completed runs'))
        self.batch_results = QTableWidget(0, 4)
        self.batch_results.setHorizontalHeaderLabels(['Run', 'Result', 'Rejected frequencies', 'Run report'])
        self.batch_results.verticalHeader().setVisible(False)
        self.batch_results.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        batch_layout.addWidget(self.batch_results)
        batch_layout.addWidget(QLabel('Comparison across completed runs'))
        self.batch_comparison = QTableWidget(0, 8)
        self.batch_comparison.setHorizontalHeaderLabels(['Hz', 'Sensor', 'Evaluated', 'Pass / Fail',
                                                        'Amplitude range', 'SNR range (dB)',
                                                        'Noise RMS range', 'Rejected runs'])
        self.batch_comparison.verticalHeader().setVisible(False)
        self.batch_comparison.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        batch_layout.addWidget(self.batch_comparison)
        self.result_tabs.addTab(batch_view, 'Batch comparison')
        self.result_tabs.setMinimumHeight(150)
        self.plot_scroll = QScrollArea()
        self.plot_scroll.setWidgetResizable(True)
        self.plot_scroll.setWidget(plot_panel)
        self.plot_scroll.setMinimumHeight(180)
        captured_page = QWidget()
        captured_layout = QVBoxLayout(captured_page)
        captured_layout.setContentsMargins(0, 0, 0, 0)
        captured_controls = QHBoxLayout()
        captured_controls.addWidget(QLabel('Captured frequency:'))
        self.chart_frequency = QComboBox()
        self.chart_frequency.currentIndexChanged.connect(self.show_captured_chart)
        captured_controls.addWidget(self.chart_frequency)
        captured_controls.addWidget(QLabel('Display:'))
        self.chart_reference = QComboBox()
        self.chart_reference.addItem('Baseline-referenced', 'baseline_mean')
        self.chart_reference.addItem('Raw values', 'raw')
        self.chart_reference.currentIndexChanged.connect(self.show_captured_chart)
        captured_controls.addWidget(self.chart_reference)
        captured_controls.addStretch()
        captured_layout.addLayout(captured_controls)
        self.captured_label = QLabel('Gray: baseline | Color: response | Time starts at each capture')
        self.captured_label.setWordWrap(True)
        captured_layout.addWidget(self.captured_label)
        captured_panel = QWidget()
        captured_grid = QGridLayout(captured_panel)
        captured_grid.setContentsMargins(0, 0, 0, 0)
        self.captured_plot_widgets = []
        self.baseline_curves = []
        self.response_curves = []
        for idx in range(8):
            graph = pg.PlotWidget(title=f'Sensor {idx + 1}')
            graph.setMinimumHeight(100)
            graph.setLabel('bottom', 'Time from capture start', units='s')
            graph.setMouseEnabled(x=False, y=True)
            graph.showGrid(x=True, y=True, alpha=0.25)
            self.baseline_curves.append(graph.plot(pen=pg.mkPen('#b0b0b0', width=1.2)))
            self.response_curves.append(graph.plot(pen=pg.mkPen(pg.intColor(idx, hues=8), width=1.8)))
            self.captured_plot_widgets.append(graph)
            captured_grid.addWidget(graph, idx // 2, idx % 2)
        self.captured_scroll = QScrollArea()
        self.captured_scroll.setWidgetResizable(True)
        self.captured_scroll.setWidget(captured_panel)
        captured_layout.addWidget(self.captured_scroll)
        self.chart_tabs = QTabWidget()
        self.chart_tabs.addTab(self.plot_scroll, 'Live charts')
        self.chart_tabs.addTab(captured_page, 'Captured test charts')
        self.chart_tabs.setMinimumHeight(180)
        self.plots_splitter = QSplitter(Qt.Orientation.Vertical)
        self.plots_splitter.setChildrenCollapsible(False)
        self.plots_splitter.addWidget(self.chart_tabs)
        self.plots_splitter.addWidget(self.result_tabs)
        self.plots_splitter.setStretchFactor(0, 3)
        self.plots_splitter.setStretchFactor(1, 1)
        self.plots_splitter.setSizes([600, 230])
        layout.addWidget(self.plots_splitter, 1)
        self.reset_results()
        self.update_controls()

        self.plot_timer = QTimer(self)
        self.plot_timer.timeout.connect(self.render)
        self.plot_timer.start(50)  # 20 Hz drawing; reader runs independently
        self.test_timer = QTimer(self)
        self.test_timer.timeout.connect(self.advance_test)
        self.test_timer.start(20)

    def refresh_ports(self):
        old = self.port.currentData()
        devices = list(list_ports.comports())
        self.port.clear()
        for device in devices:
            self.port.addItem(f'{device.device} — {device.description}', device.device)
        if old is not None:
            idx = self.port.findData(old)
            if idx >= 0:
                self.port.setCurrentIndex(idx)
                return
        for idx, device in enumerate(devices):
            if '16C0:0483' in device.hwid.upper():
                self.port.setCurrentIndex(idx)
                break

    def update_controls(self):
        running = self.test_state in self.ACTIVE_STATES
        self.port.setEnabled(self.worker is None)
        self.refresh_button.setEnabled(self.worker is None)
        self.connect_button.setText('Disconnect' if self.worker is not None else 'Connect')
        self.baseline_button.setEnabled(self.is_connected and not running)
        self.response_button.setEnabled(self.is_connected and not running)
        self.frequency.setEnabled(self.is_connected and not running)
        self.frequency_sequence.setEnabled(not running)
        self.batch_count_control.setEnabled(not running)
        self.all_frequencies_button.setEnabled(not running)
        self.timing_button.setEnabled(not running and not self.config_error)
        self.run_button.setEnabled(self.is_connected and not running and not self.config_error)
        self.abort_button.setEnabled(self.is_connected and running)
        self.min_amplitude.setEnabled(not running)
        self.min_snr.setEnabled(not running)
        self.save_button.setEnabled(not running and not self.config_error)

    def toggle_connection(self):
        if self.worker is not None:
            if self.test_state in self.ACTIVE_STATES:
                self.abort_test()
            worker = self.worker
            worker.stop()
            return  # on_worker_finished() restores UI
        port = self.port.currentData()
        if not port:
            QMessageBox.warning(self, 'No serial port', 'Select the Teensy COM port.')
            return
        self.history = [deque(maxlen=1000) for _ in range(8)]
        self.last_command = 'None'
        worker = SerialWorker(port)
        self.worker = worker
        worker.sample.connect(self.on_sample)
        worker.malformed.connect(self.on_malformed)
        worker.device_message.connect(self.on_device_message)
        worker.connected.connect(self.on_connected)
        worker.status.connect(self.on_status)
        worker.command_sent.connect(self.on_command_sent)
        worker.finished.connect(self.on_worker_finished)
        worker.start()
        self.update_controls()

    def on_connected(self, connected: bool):
        self.is_connected = connected
        if not connected:
            self.actuator_label.setText('Actuator requested: UNKNOWN (disconnected; no hardware feedback)')
        if not connected and self.test_state in self.ACTIVE_STATES:
            self.finish_test_error('Serial connection lost')
        self.update_controls()

    def on_status(self, message: str):
        self.connection_label.setText(f'{message} | Last command: {self.last_command}')

    def on_worker_finished(self):
        self.is_connected = False
        self.worker = None
        self.connection_label.setText(f'Disconnected | Last command: {self.last_command}')
        self.update_controls()

    def on_command_sent(self, command: str, sent_at: float):
        self.last_command = command
        self.connection_label.setText(f'Connected | Last command sent: {command}')
        requested = 'OFF (baseline)' if command == 'baseline' else f'ON ({command.removeprefix("response:")} Hz)'
        self.actuator_label.setText(f'Actuator requested: {requested} (no hardware feedback)')
        if command == self.expected_command:
            if self.test_state == 'WAIT_BASELINE_COMMAND':
                self.set_test_state('BASELINE_SETTLE')
                self.deadline = sent_at + self.settings['baseline_settle_s']
            elif self.test_state == 'WAIT_RESPONSE_COMMAND':
                self.set_test_state('EXCITE_SETTLE')
                self.deadline = sent_at + self.test_timing['excite_settle_s']
            self.expected_command = ''

    def on_sample(self, timestamp: float, values: tuple[float, ...]):
        for history, value in zip(self.history, values):
            history.append((timestamp, value))
        if self.test_state == 'BASELINE_CAPTURE':
            if self.capture_start <= timestamp < self.deadline:
                self.baseline_samples.append((timestamp, values))
        elif self.test_state == 'EXCITE_CAPTURE':
            if self.capture_start <= timestamp < self.deadline:
                self.excited_samples.append((timestamp, values))

    def on_malformed(self, timestamp: float):
        if (self.test_state in ('BASELINE_CAPTURE', 'EXCITE_CAPTURE')
                and self.capture_start <= timestamp < self.deadline):
            self.finish_test_error('Malformed sensor data during capture')

    def on_device_message(self, timestamp: float, message: str):
        # Firmware may report a rejected frequency and echo the old one. The
        # exercise does not guarantee acknowledgements, so absence is allowed.
        if self.test_state not in ('BASELINE_CAPTURE', 'EXCITE_SETTLE', 'EXCITE_CAPTURE'):
            return
        if message.lower() == 'response frequency invalid':
            self.record_rejected_frequency()
            return
        try:
            acknowledged = float(message)
        except ValueError:
            if (self.test_state in ('BASELINE_CAPTURE', 'EXCITE_CAPTURE')
                    and self.capture_start <= timestamp < self.deadline):
                self.finish_test_error('Unexpected text during sensor capture')
            return
        if self.test_state == 'BASELINE_CAPTURE':
            return  # A lone numeric acknowledgement is not a sensor row.
        if not math.isfinite(acknowledged) or abs(acknowledged - self.test_hz) > 0.01:
            self.finish_test_error(f'Device acknowledged {message} Hz, requested {self.test_hz} Hz')

    def baseline(self):
        if self.worker and self.is_connected and self.test_state not in self.ACTIVE_STATES:
            self.worker.send('baseline')

    def select_all_frequencies(self):
        self.frequency_sequence.setText(', '.join(str(f) for f in range(1, 26)))

    def edit_frequency_timings(self):
        dialog = FrequencyTimingsDialog(self.settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.settings['frequency_timings'] = dialog.timings
            self.statusBar().showMessage('Frequency timings applied. Click Save Settings to keep them.', 8000)

    def response(self):
        if self.worker and self.is_connected and self.test_state not in self.ACTIVE_STATES:
            self.worker.send(excitation_command(int(self.frequency.value())))

    def reset_results(self):
        self.results.setRowCount(0)

    def set_test_state(self, new_state: str, suffix: str = ''):
        self.test_state = new_state
        display = 'TEST ERROR' if new_state == 'ERROR' else f'Test: {new_state}'
        prefix = f'Run {self.batch_run_number}/{self.batch_count} | ' if self.batch_count > 1 else ''
        self.test_label.setText(prefix + display + (f' — {suffix}' if suffix else ''))
        color = {'PASS': '#166534', 'FAIL': '#b91c1c', 'ERROR': '#b91c1c'}.get(new_state, '')
        self.test_label.setStyleSheet(f'font-weight: bold; font-size: 16px; color: {color};' if color else '')
        self.update_controls()

    def start_test(self):
        if not self.is_connected or self.worker is None or self.test_state in self.ACTIVE_STATES:
            return
        if self.config_error:
            self.set_test_state('ERROR', f'Invalid settings: {self.config_error}')
            return
        try:
            text = self.frequency_sequence.text().strip()
            frequencies = [int(part.strip()) for part in text.split(',')] if text else [int(self.frequency.value())]
            test_settings = deepcopy(self.settings)
            test_settings['min_amplitude'] = float(self.min_amplitude.value())
            test_settings['min_snr_db'] = float(self.min_snr.value())
            test_settings['test_frequencies_hz'] = frequencies
            self.test_settings = normalize_settings(test_settings)
            for f in frequencies:
                if resolve_timing(self.test_settings, f)['excite_capture_s'] * f < 2:
                    raise ValueError(f'{f} Hz capture must cover at least two cycles')
        except ValueError as exc:
            self.set_test_state('ERROR', str(exc))
            return
        self.baseline_samples = []
        self.excited_samples = []
        self.frequency_runs = []
        self.last_results = None
        self.test_frequencies = frequencies
        self.frequency_index = 0
        self.test_hz = frequencies[0]
        self.test_min_amplitude = float(self.min_amplitude.value())
        self.test_min_snr_db = float(self.min_snr.value())
        self.batch_count = int(self.batch_count_control.value())
        self.batch_run_number = 1
        self.batch_runs = []
        self.batch_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
        self.batch_results.setRowCount(0)
        self.batch_comparison.setRowCount(0)
        self.result_tabs.setCurrentIndex(0)
        self.reset_results()
        self.clear_captured_charts()
        self.expected_command = 'baseline'
        self.command_wait_start = time.perf_counter()
        self.set_test_state('WAIT_BASELINE_COMMAND', 'Requesting actuator OFF')
        self.worker.send('baseline')

    def advance_test(self):
        now = time.perf_counter()
        if self.test_state in ('WAIT_BASELINE_COMMAND', 'WAIT_RESPONSE_COMMAND'):
            if now - self.command_wait_start > 2.0:
                self.finish_test_error('Serial write timed out')
        elif self.test_state == 'BASELINE_SETTLE' and now >= self.deadline:
            self.begin_capture('BASELINE_CAPTURE', self.settings['baseline_capture_s'])
        elif self.test_state == 'BASELINE_CAPTURE' and now >= self.deadline:
            try:
                self.check_capture(self.baseline_samples, 'baseline_capture_s', 'baseline')
            except ValueError as exc:
                self.finish_test_error(str(exc))
                return
            self.request_response(now)
        elif self.test_state == 'EXCITE_SETTLE' and now >= self.deadline:
            self.begin_capture('EXCITE_CAPTURE', self.test_timing['excite_capture_s'])
        elif self.test_state == 'EXCITE_CAPTURE' and now >= self.deadline:
            self.complete_test()
        elif self.test_state == 'WAIT_REJECTED' and now >= self.deadline:
            self.frequency_index += 1
            if self.frequency_index < len(self.test_frequencies):
                self.request_response(now)
            else:
                self.finish_sequence()

    def required_samples(self, duration_key: str) -> int:
        # Allow overhead/dropouts, but refuse meaningless tiny captures.
        duration = (self.test_timing['excite_capture_s'] if duration_key == 'excite_capture_s'
                    else self.test_settings[duration_key])
        return max(40, int(0.6 * self.test_settings['nominal_sample_rate_hz'] * duration))

    def begin_capture(self, state: str, duration: float):
        self.capture_start = time.perf_counter()
        self.deadline = self.capture_start + duration
        suffix = f'{duration:g} seconds'
        if state == 'EXCITE_CAPTURE':
            suffix = f'{self.frequency_index + 1}/{len(self.test_frequencies)}: {self.test_hz} Hz | {suffix}'
        self.set_test_state(state, suffix)

    def check_capture(self, samples, duration_key, label):
        max_gap = self.test_settings['max_sample_gap_s']
        rate = validate_capture(samples, self.required_samples(duration_key),
                                self.test_settings['min_receive_rate_hz'],
                                self.test_settings['max_receive_rate_hz'], max_gap, label)
        if (samples[0][0] - self.capture_start > max_gap
                or self.deadline - samples[-1][0] > max_gap):
            raise ValueError(f'{label} timing gap at capture boundary exceeds {max_gap:.3f} s')
        return rate

    def request_response(self, now):
        self.excited_samples = []
        self.test_hz = self.test_frequencies[self.frequency_index]
        self.test_timing = resolve_timing(self.test_settings, self.test_hz)
        self.expected_command = excitation_command(self.test_hz)
        self.command_wait_start = now
        self.set_test_state('WAIT_RESPONSE_COMMAND',
                            f'{self.frequency_index + 1}/{len(self.test_frequencies)}: requesting {self.test_hz} Hz '
                            f'| settle {self.test_timing["excite_settle_s"]:g} s, '
                            f'capture {self.test_timing["excite_capture_s"]:g} s')
        if self.worker:
            self.worker.send(self.expected_command)

    def record_rejected_frequency(self):
        frequency = self.test_hz
        reason = f'Device rejected {frequency} Hz excitation; sensors not evaluated'
        self.frequency_runs.append({'frequency_hz': frequency, 'status': 'REJECTED', 'timing': self.test_timing.copy(),
                                    'reason': reason, 'samples': [], 'rate': None, 'results': []})
        row = self.results.rowCount()
        self.results.insertRow(row)
        for col, value in enumerate([str(frequency), '—', '—', '—', '—', '—', 'REJECTED', reason]):
            cell = QTableWidgetItem(value)
            cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if col == 6:
                cell.setBackground(QColor('#fef3c7'))
                cell.setForeground(QColor('#92400e'))
            self.results.setItem(row, col, cell)
        # Some firmware follows the rejection with an echo of the previous
        # frequency. Drain it before sending the next command.
        self.expected_command = ''
        self.deadline = time.perf_counter() + 0.3
        self.set_test_state('WAIT_REJECTED', reason)
        if self.worker and self.is_connected:
            self.worker.send('baseline')

    def complete_test(self):
        self.set_test_state('EVALUATING')
        try:
            baseline_required = self.required_samples('baseline_capture_s')
            excited_required = self.required_samples('excite_capture_s')
            rate = self.check_capture(self.excited_samples, 'excite_capture_s', f'{self.test_hz} Hz excited')
            receive_timing = receive_timing_stats(self.excited_samples)
            if rate <= 2 * self.test_hz:
                raise ValueError(f'Receive rate too low for {self.test_hz} Hz')
            limits = [resolve_limits(self.test_settings, self.test_hz, i) for i in range(1, 9)]
            output = evaluate(self.baseline_samples, self.excited_samples,
                              self.test_hz, self.test_min_amplitude,
                              self.test_min_snr_db,
                              min_samples=min(baseline_required, excited_required),
                              channel_limits=limits)
            self.last_results = output
            self.frequency_runs.append({'frequency_hz': self.test_hz, 'status': 'EVALUATED', 'timing': self.test_timing.copy(),
                                        'samples': self.excited_samples, 'rate': rate,
                                        'receive_timing': receive_timing, 'results': output})
            self.add_captured_chart(self.test_hz, self.baseline_samples, self.excited_samples)
            for result in output:
                row = self.results.rowCount()
                self.results.insertRow(row)
                lim = result.limits
                optional = lambda value: 'off' if value is None else f'{value:g}'
                values = [str(self.test_hz), str(result.channel),
                          f'{result.baseline_noise_rms:.2f} / {optional(lim["max_baseline_noise"])}',
                          f'{result.response_amplitude:.2f} / {lim["min_amplitude"]:g}–{optional(lim["max_response_amplitude"])}',
                          f'{result.snr_db:.1f} / {lim["min_snr_db"]:g}',
                          f'{result.signal_min:.1f}–{result.signal_max:.1f} / {optional(lim["clip_min"])}–{optional(lim["clip_max"])}',
                          result.result, result.reason]
                for col, value in enumerate(values):
                    cell = QTableWidgetItem(value)
                    cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    if col == 6:
                        cell.setBackground(QColor('#bbf7d0' if result.result == 'PASS'
                                                  else '#fecaca'))
                        cell.setForeground(QColor('#14532d' if result.result == 'PASS'
                                                  else '#7f1d1d'))
                        cell.setToolTip(result.reason)
                    self.results.setItem(row, col, cell)
            self.rate_label.setText(f'Host receive rate: {rate:.1f} Hz '
                                    f'| interval p95: {receive_timing["p95_interval_ms"]:.1f} ms '
                                    f'| max gap: {receive_timing["maximum_gap_ms"]:.1f} ms '
                                    f'| baseline {len(self.baseline_samples)} / excited {len(self.excited_samples)} samples')
            self.frequency_index += 1
            if self.frequency_index < len(self.test_frequencies):
                self.request_response(time.perf_counter())
                return
            self.finish_sequence()
            return
        except (ValueError, OSError) as exc:
            reason = self.record_interrupted_batch('ERROR', str(exc))
            self.set_test_state('ERROR', reason)
        self.expected_command = ''
        if self.worker and self.is_connected:
            self.worker.send('baseline')  # request actuator OFF when finished or on error

    def finish_sequence(self):
        rejected = [run['frequency_hz'] for run in self.frequency_runs if run['status'] == 'REJECTED']
        overall = ('ERROR' if rejected else
                   'PASS' if all(result.result == 'PASS' for run in self.frequency_runs
                                 for result in run['results']) else 'FAIL')
        try:
            saved = self.log_results(overall)
            if self.batch_count > 1:
                self.batch_runs.append({
                    'run_number': self.batch_run_number,
                    'overall_result': overall,
                    'run_report': saved,
                    'frequency_results': [
                        {'frequency_hz': run['frequency_hz'], 'status': run['status'],
                         'sensors': [vars(result).copy() for result in run['results']]}
                        for run in self.frequency_runs],
                })
                self.update_batch_view()
                final_run = self.batch_run_number == self.batch_count
                summary = self.write_batch_summary('COMPLETE' if final_run else 'IN_PROGRESS')
                if not final_run:
                    self.batch_run_number += 1
                    self.baseline_samples = []
                    self.excited_samples = []
                    self.frequency_runs = []
                    self.last_results = None
                    self.frequency_index = 0
                    self.test_hz = self.test_frequencies[0]
                    self.reset_results()
                    self.clear_captured_charts()
                    self.expected_command = 'baseline'
                    self.command_wait_start = time.perf_counter()
                    self.set_test_state('WAIT_BASELINE_COMMAND', 'Requesting a fresh baseline')
                    if self.worker and self.is_connected:
                        self.worker.send('baseline')
                    return
                batch_overall = ('ERROR' if any(run['overall_result'] == 'ERROR' for run in self.batch_runs) else
                                 'FAIL' if any(run['overall_result'] == 'FAIL' for run in self.batch_runs) else 'PASS')
                self.result_tabs.setCurrentIndex(1)
                self.set_test_state(batch_overall, f'Batch complete: {len(self.batch_runs)} runs | saved: {summary}')
                self.expected_command = ''
                if self.worker and self.is_connected:
                    self.worker.send('baseline')
                return
            detail = ('Device rejected: ' + ', '.join(f'{f} Hz' for f in rejected)
                      if rejected else f'{len(self.frequency_runs)} frequency capture(s)')
            self.set_test_state(overall, f'{detail} | saved: {saved}')
        except OSError as exc:
            reason = self.record_interrupted_batch('ERROR', f'Could not save results: {exc}')
            self.set_test_state('ERROR', reason)
        self.expected_command = ''
        if self.worker and self.is_connected:
            self.worker.send('baseline')

    def log_results(self, overall: str) -> str:
        directory = ROOT / 'runs'
        directory.mkdir(parents=True, exist_ok=True)
        suffix = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
        stem = f'test_{suffix}'
        csv_file = directory / f'{stem}.csv'
        json_file = directory / f'{stem}.json'
        first_timestamp = self.baseline_samples[0][0]
        with csv_file.open('w', encoding='utf-8', newline='') as handle:
            writer = csv.writer(handle)
            writer.writerow(['phase', 'frequency_hz', 'relative_time_s'] + [f'sensor_{i}' for i in range(1, 9)])
            captures = [('baseline', '', self.baseline_samples)] + [
                ('excited', run['frequency_hz'], run['samples']) for run in self.frequency_runs]
            for name, frequency, samples in captures:
                for timestamp, values in samples:
                    writer.writerow([name, frequency, f'{timestamp - first_timestamp:.6f}', *values])
        with json_file.open('w', encoding='utf-8') as handle:
            json.dump({
                'utc_timestamp': datetime.now(timezone.utc).isoformat(),
                'batch_id': self.batch_id if self.batch_count > 1 else None,
                'batch_run_number': self.batch_run_number if self.batch_count > 1 else None,
                'port': self.port.currentData(),
                'frequencies_hz': self.test_frequencies,
                'baseline_sample_rate_hz': sample_rate_hz(self.baseline_samples),
                'baseline_receive_timing': receive_timing_stats(self.baseline_samples),
                'receive_clock': 'time.perf_counter',
                'receive_clock_resolution_s': time.get_clock_info('perf_counter').resolution,
                'nominal_sample_rate_hz': self.settings['nominal_sample_rate_hz'],
                'baseline_settle_s': self.settings['baseline_settle_s'],
                'baseline_capture_s': self.settings['baseline_capture_s'],
                'excite_settle_s': self.settings['excite_settle_s'],
                'excite_capture_s': self.settings['excite_capture_s'],
                'baseline_sample_count': len(self.baseline_samples),
                'overall_result': overall,
                'validity_criteria': {key: self.test_settings[key] for key in
                                      ('min_receive_rate_hz', 'max_receive_rate_hz', 'max_sample_gap_s')},
                'frequency_results': [
                    {'frequency_hz': run['frequency_hz'], 'status': run['status'],
                     'excite_settle_s': run['timing']['excite_settle_s'],
                     'excite_capture_s': run['timing']['excite_capture_s'],
                     'reason': run.get('reason', ''), 'estimated_sample_rate_hz': run['rate'],
                     'receive_timing': run.get('receive_timing'),
                     'sample_count': len(run['samples']),
                     'sensors': [vars(result) for result in run['results']]}
                    for run in self.frequency_runs],
                'raw_csv': csv_file.name,
            }, handle, indent=2)
        return str(json_file.relative_to(ROOT))

    def update_batch_view(self):
        self.batch_results.setRowCount(0)
        for run in self.batch_runs:
            row = self.batch_results.rowCount()
            self.batch_results.insertRow(row)
            rejected = [str(f['frequency_hz']) for f in run['frequency_results'] if f['status'] == 'REJECTED']
            values = [str(run['run_number']), run['overall_result'], ', '.join(rejected) or '—', run['run_report']]
            for col, value in enumerate(values):
                self.batch_results.setItem(row, col, QTableWidgetItem(value))

        self.batch_comparison.setRowCount(0)
        def span(value):
            return '—' if value is None else f'{value[0]:.2f} – {value[1]:.2f}'
        for comparison in compare_runs(self.batch_runs, self.test_frequencies):
            row = self.batch_comparison.rowCount()
            self.batch_comparison.insertRow(row)
            values = [str(comparison['frequency_hz']), str(comparison['channel']),
                      str(comparison['evaluated_runs']),
                      f'{comparison["pass_count"]} / {comparison["fail_count"]}',
                      span(comparison['response_amplitude_range']), span(comparison['snr_db_range']),
                      span(comparison['baseline_noise_rms_range']),
                      ', '.join(str(i) for i in comparison['rejected_runs']) or '—']
            for col, value in enumerate(values):
                self.batch_comparison.setItem(row, col, QTableWidgetItem(value))

    def write_batch_summary(self, status, reason=''):
        directory = ROOT / 'runs'
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f'batch_{self.batch_id}.json'
        temporary = target.with_suffix('.json.tmp')
        outcomes = [run['overall_result'] for run in self.batch_runs]
        batch_overall = ('ERROR' if 'ERROR' in outcomes else 'FAIL' if 'FAIL' in outcomes else 'PASS')
        if status != 'COMPLETE':
            batch_overall = None
        data = {
            'batch_id': self.batch_id, 'status': status, 'overall_result': batch_overall,
            'requested_runs': self.batch_count, 'completed_runs': len(self.batch_runs),
            'frequencies_hz': self.test_frequencies, 'runs': self.batch_runs,
            'comparison': compare_runs(self.batch_runs, self.test_frequencies),
            'interrupted_run': self.batch_run_number if status in ('ERROR', 'ABORTED') else None,
            'reason': reason,
        }
        temporary.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
        temporary.replace(target)
        return str(target.relative_to(ROOT))

    def record_interrupted_batch(self, status, reason):
        if self.batch_count > 1:
            try:
                path = self.write_batch_summary(status, reason)
                return f'{reason} | batch saved: {path}'
            except OSError as exc:
                return f'{reason} | could not save batch: {exc}'
        return reason

    def finish_test_error(self, reason: str):
        if self.test_state not in self.ACTIVE_STATES:
            return
        self.expected_command = ''
        self.set_test_state('ERROR', self.record_interrupted_batch('ERROR', reason))
        if self.worker and self.is_connected:
            self.worker.send('baseline')

    def abort_test(self):
        if self.test_state in self.ACTIVE_STATES:
            self.expected_command = ''
            reason = self.record_interrupted_batch('ABORTED', 'Operator aborted; requesting baseline')
            self.set_test_state('ABORTED', reason)
            if self.worker and self.is_connected:
                self.worker.send('baseline')

    def save_settings(self):
        self.settings['default_frequency_hz'] = int(self.frequency.value())
        self.settings['min_amplitude'] = float(self.min_amplitude.value())
        self.settings['min_snr_db'] = float(self.min_snr.value())
        try:
            text = self.frequency_sequence.text().strip()
            self.settings['test_frequencies_hz'] = [int(part.strip()) for part in text.split(',')] if text else []
            self.settings = normalize_settings(self.settings)
            SETTINGS_PATH.write_text(json.dumps(self.settings, indent=2) + '\n', encoding='utf-8')
            self.statusBar().showMessage(f'Saved {SETTINGS_PATH}', 5000)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, 'Could not save settings', str(exc))

    def render(self):
        for curve, history in zip(self.curves, self.history):
            if history:
                latest = history[-1][0]
                recent = [(t - latest, value) for t, value in history if t >= latest - 5]
                curve.setData([t for t, _ in recent], [value for _, value in recent])

    def clear_captured_charts(self):
        self.chart_captures = {}
        self.chart_frequency.clear()
        for curve in self.baseline_curves + self.response_curves:
            curve.clear()
        self.captured_label.setText('Gray: baseline | Color: response | Time starts at each capture')

    def add_captured_chart(self, frequency, baseline, excited):
        self.chart_captures[frequency] = (baseline, excited)
        self.chart_frequency.addItem(f'{frequency} Hz', frequency)
        self.chart_frequency.setCurrentIndex(self.chart_frequency.count() - 1)
        self.show_captured_chart()

    def show_captured_chart(self):
        frequency = self.chart_frequency.currentData()
        capture = self.chart_captures.get(frequency)
        if not capture:
            return
        baseline, excited = capture
        base_time = [timestamp - baseline[0][0] for timestamp, _ in baseline]
        excite_time = [timestamp - excited[0][0] for timestamp, _ in excited]
        referenced = self.chart_reference.currentData() == 'baseline_mean'
        for channel in range(8):
            offset = math.fsum(values[channel] for _, values in baseline) / len(baseline) if referenced else 0.0
            self.baseline_curves[channel].setData(base_time, [values[channel] - offset for _, values in baseline])
            self.response_curves[channel].setData(excite_time, [values[channel] - offset for _, values in excited])
        run = f'Run {self.batch_run_number}: ' if self.batch_count > 1 else ''
        description = ('Gray: baseline − baseline mean | Color: response − baseline mean'
                       if referenced else 'Gray: raw baseline | Color: raw response')
        self.captured_label.setText(f'{run}{frequency} Hz | {description} | Time starts at each capture')

    def chart_image(self):
        """Render all eight graphs, including those outside the scroll viewport."""
        captured = self.chart_tabs.currentIndex() == 1
        if captured and not self.chart_captures:
            raise ValueError('No captured test charts are available yet.')
        if not captured and not any(self.history):
            raise ValueError('No live sensor data is available yet.')
        graphs = self.captured_plot_widgets if captured else self.plot_widgets
        if captured:
            title = self.captured_label.text()
        else:
            title = f'Live sensor charts | Last command: {self.last_command}'
        # Give every panel a useful export size, independent of scroll position.
        panel_width = max(450, max(graph.width() for graph in graphs))
        panel_height = max(130, max(graph.height() for graph in graphs))
        margin, gap, heading = 12, 8, 38
        width = 2 * margin + 2 * panel_width + gap
        height = heading + margin + 4 * panel_height + 3 * gap
        result = QImage(width, height, QImage.Format.Format_RGB32)
        result.fill(QColor('white'))
        painter = QPainter(result)
        try:
            painter.setPen(QColor('#111827'))
            painter.drawText(QRect(margin, 0, width - 2 * margin, heading),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, title)
            for idx, graph in enumerate(graphs):
                x = margin + (idx % 2) * (panel_width + gap)
                y = heading + (idx // 2) * (panel_height + gap)
                graph.render(painter, QRectF(x, y, panel_width, panel_height),
                             graph.viewport().rect(), Qt.AspectRatioMode.IgnoreAspectRatio)
        finally:
            painter.end()
        return result

    def export_chart(self):
        try:
            image = self.chart_image()
        except ValueError as exc:
            QMessageBox.warning(self, 'No chart data', str(exc))
            return
        name = 'captured_chart.png' if self.chart_tabs.currentIndex() == 1 else 'live_chart.png'
        path, _ = QFileDialog.getSaveFileName(self, 'Export charts', str(ROOT / name), 'PNG image (*.png)')
        if not path:
            return
        if not path.lower().endswith('.png'):
            path += '.png'
        if not image.save(path, 'PNG'):
            QMessageBox.warning(self, 'Could not export charts', f'Could not write {path}')
            return
        self.statusBar().showMessage(f'Exported charts to {path}', 8000)

    def closeEvent(self, event):
        if self.worker:
            if self.test_state in self.ACTIVE_STATES:
                self.abort_test()
            worker = self.worker
            worker.stop()
        event.accept()


if __name__ == '__main__':
    smoke_test = '--smoke-test' in sys.argv[1:]
    if smoke_test:
        os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    app = QApplication([sys.argv[0]] if smoke_test else sys.argv)
    window = MainWindow()
    window.show()
    if smoke_test:
        QTimer.singleShot(500, app.quit)
    sys.exit(app.exec())
