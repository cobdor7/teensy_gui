"""Eight-channel sensor test station. Only ASCII commands are sent to Teensy."""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import csv
import json
from pathlib import Path
import queue
import sys
import threading
import time

import pyqtgraph as pg
import serial
from serial.tools import list_ports
from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDoubleSpinBox, QGridLayout, QHBoxLayout,
    QLabel, QMainWindow, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget, QHeaderView,
)

from analysis import evaluate, sample_rate_hz
from protocol import excitation_command, parse_sample


ROOT = Path(sys.executable if getattr(sys, 'frozen', False) else __file__).resolve().parent
SETTINGS_PATH = ROOT / 'settings.json'
DEFAULT_SETTINGS = {
    'default_frequency_hz': 10,
    'baseline_settle_s': 0.5,
    'baseline_capture_s': 2.0,
    'excite_settle_s': 0.5,
    'excite_capture_s': 2.0,
    'min_amplitude': 100.0,
    'min_snr_db': 15.0,
    'nominal_sample_rate_hz': 100,
}


def load_settings() -> dict:
    if not SETTINGS_PATH.exists():
        return DEFAULT_SETTINGS.copy()
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding='utf-8'))
        settings = DEFAULT_SETTINGS | data
        for key in ('baseline_settle_s', 'baseline_capture_s',
                    'excite_settle_s', 'excite_capture_s'):
            if not 0.1 <= float(settings[key]) <= 30:
                raise ValueError(f'Invalid {key}')
        if not 1 <= int(settings['default_frequency_hz']) <= 25:
            raise ValueError('Invalid default_frequency_hz')
        if float(settings['min_amplitude']) < 0:
            raise ValueError('Invalid min_amplitude')
        if int(settings['nominal_sample_rate_hz']) < 10:
            raise ValueError('Invalid nominal_sample_rate_hz')
        return settings
    except (OSError, ValueError, TypeError) as exc:
        print(f'WARNING: Could not load settings.json ({exc}); using defaults.', file=sys.stderr)
        return DEFAULT_SETTINGS.copy()


class SerialWorker(QThread):
    sample = Signal(float, object)         # monotonic arrival timestamp, 8 floats
    connected = Signal(bool)
    status = Signal(str)
    command_sent = Signal(str, float)      # actual write completed

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
                            now = time.monotonic()
                            self.command_sent.emit(command, now)
                    except queue.Empty:
                        pass

                    raw = conn.readline()
                    if not raw:
                        continue
                    timestamp = time.monotonic()
                    values = parse_sample(raw.decode('ascii', errors='replace'))
                    if values is not None:
                        self.sample.emit(timestamp, values)
                    # Ignore one-value frequency acknowledgements and other text.
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
        'WAIT_RESPONSE_COMMAND', 'EXCITE_SETTLE', 'EXCITE_CAPTURE',
    }

    def __init__(self):
        super().__init__()
        self.settings = load_settings()
        self.setWindowTitle('Sensor Test Station — Automated Evaluation')
        self.resize(1250, 1020)
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
        self.baseline_button = QPushButton('Baseline / OFF')
        self.baseline_button.clicked.connect(self.baseline)
        self.response_button = QPushButton('Excite / ON')
        self.response_button.clicked.connect(self.response)
        self.run_button = QPushButton('RUN AUTOMATIC TEST')
        self.run_button.clicked.connect(self.start_test)
        self.abort_button = QPushButton('Abort Test')
        self.abort_button.clicked.connect(self.abort_test)
        for w in (QLabel('Frequency:'), self.frequency, self.baseline_button,
                  self.response_button, self.run_button, self.abort_button):
            controls.addWidget(w)
        layout.addLayout(controls)

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
        self.test_label = QLabel('Test: IDLE')
        self.rate_label = QLabel('Estimated sample rate: —')
        layout.addWidget(self.connection_label)
        layout.addWidget(self.test_label)
        layout.addWidget(self.rate_label)

        plots = QGridLayout()
        self.curves = []
        for idx in range(8):
            graph = pg.PlotWidget(title=f'Sensor {idx + 1}')
            graph.setLabel('bottom', 'Recent time', units='s')
            graph.setXRange(-5, 0, padding=0)
            graph.setMouseEnabled(x=False, y=True)
            graph.showGrid(x=True, y=True, alpha=0.25)
            curve = graph.plot(pen=pg.mkPen(pg.intColor(idx, hues=8), width=1.8))
            self.curves.append(curve)
            plots.addWidget(graph, idx // 2, idx % 2)
        layout.addLayout(plots, 1)

        self.results = QTableWidget(8, 5)
        self.results.setHorizontalHeaderLabels(
            ['Sensor', 'Baseline noise RMS', 'Response amplitude', 'SNR (dB)', 'Result']
        )
        self.results.verticalHeader().setVisible(False)
        self.results.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.results.setMinimumHeight(220)
        layout.addWidget(self.results)
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
        self.run_button.setEnabled(self.is_connected and not running)
        self.abort_button.setEnabled(self.is_connected and running)
        self.min_amplitude.setEnabled(not running)
        self.min_snr.setEnabled(not running)
        self.save_button.setEnabled(not running)

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
        worker = SerialWorker(port)
        self.worker = worker
        worker.sample.connect(self.on_sample)
        worker.connected.connect(self.on_connected)
        worker.status.connect(self.on_status)
        worker.command_sent.connect(self.on_command_sent)
        worker.finished.connect(self.on_worker_finished)
        worker.start()
        self.update_controls()

    def on_connected(self, connected: bool):
        self.is_connected = connected
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
        if command == self.expected_command:
            if self.test_state == 'WAIT_BASELINE_COMMAND':
                self.set_test_state('BASELINE_SETTLE')
                self.deadline = sent_at + self.settings['baseline_settle_s']
            elif self.test_state == 'WAIT_RESPONSE_COMMAND':
                self.set_test_state('EXCITE_SETTLE')
                self.deadline = sent_at + self.settings['excite_settle_s']
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

    def baseline(self):
        if self.worker and self.is_connected and self.test_state not in self.ACTIVE_STATES:
            self.worker.send('baseline')

    def response(self):
        if self.worker and self.is_connected and self.test_state not in self.ACTIVE_STATES:
            self.worker.send(excitation_command(int(self.frequency.value())))

    def reset_results(self):
        for row in range(8):
            for col, value in enumerate([str(row + 1), '—', '—', '—', '—']):
                cell = QTableWidgetItem(value)
                cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.results.setItem(row, col, cell)

    def set_test_state(self, new_state: str, suffix: str = ''):
        self.test_state = new_state
        self.test_label.setText(f'Test: {new_state}' + (f' — {suffix}' if suffix else ''))
        self.update_controls()

    def start_test(self):
        if not self.is_connected or self.worker is None or self.test_state in self.ACTIVE_STATES:
            return
        self.baseline_samples = []
        self.excited_samples = []
        self.last_results = None
        self.test_hz = int(self.frequency.value())
        self.test_min_amplitude = float(self.min_amplitude.value())
        self.test_min_snr_db = float(self.min_snr.value())
        self.reset_results()
        self.expected_command = 'baseline'
        self.command_wait_start = time.monotonic()
        self.set_test_state('WAIT_BASELINE_COMMAND', 'Requesting actuator OFF')
        self.worker.send('baseline')

    def advance_test(self):
        now = time.monotonic()
        if self.test_state in ('WAIT_BASELINE_COMMAND', 'WAIT_RESPONSE_COMMAND'):
            if now - self.command_wait_start > 2.0:
                self.finish_test_error('Serial write timed out')
        elif self.test_state == 'BASELINE_SETTLE' and now >= self.deadline:
            self.begin_capture('BASELINE_CAPTURE', self.settings['baseline_capture_s'])
        elif self.test_state == 'BASELINE_CAPTURE' and now >= self.deadline:
            if len(self.baseline_samples) < self.required_samples('baseline_capture_s'):
                self.finish_test_error(f'Insufficient baseline samples ({len(self.baseline_samples)})')
                return
            self.expected_command = excitation_command(self.test_hz)
            self.command_wait_start = now
            self.set_test_state('WAIT_RESPONSE_COMMAND', f'Requesting {self.test_hz} Hz')
            if self.worker:
                self.worker.send(self.expected_command)
        elif self.test_state == 'EXCITE_SETTLE' and now >= self.deadline:
            self.begin_capture('EXCITE_CAPTURE', self.settings['excite_capture_s'])
        elif self.test_state == 'EXCITE_CAPTURE' and now >= self.deadline:
            self.complete_test()

    def required_samples(self, duration_key: str) -> int:
        # Allow overhead/dropouts, but refuse meaningless tiny captures.
        return max(40, int(0.6 * self.settings['nominal_sample_rate_hz'] *
                           self.settings[duration_key]))

    def begin_capture(self, state: str, duration: float):
        self.capture_start = time.monotonic()
        self.deadline = self.capture_start + duration
        self.set_test_state(state, f'{duration:g} seconds')

    def complete_test(self):
        self.set_test_state('EVALUATING')
        try:
            baseline_required = self.required_samples('baseline_capture_s')
            excited_required = self.required_samples('excite_capture_s')
            if len(self.baseline_samples) < baseline_required:
                raise ValueError('Insufficient baseline samples')
            if len(self.excited_samples) < excited_required:
                raise ValueError(f'Insufficient excited samples ({len(self.excited_samples)})')
            output = evaluate(self.baseline_samples, self.excited_samples,
                              self.test_hz, self.test_min_amplitude,
                              self.test_min_snr_db,
                              min_samples=min(baseline_required, excited_required))
            self.last_results = output
            for row, result in enumerate(output):
                values = [str(result.channel), f'{result.baseline_noise_rms:.2f}',
                          f'{result.response_amplitude:.2f}',
                          f'{result.snr_db:.1f}', result.result]
                for col, value in enumerate(values):
                    cell = QTableWidgetItem(value)
                    cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    if col == 4:
                        cell.setBackground(QColor('#bbf7d0' if result.result == 'PASS'
                                                  else '#fecaca'))
                        cell.setForeground(QColor('#14532d' if result.result == 'PASS'
                                                  else '#7f1d1d'))
                        cell.setToolTip(result.reason)
                    self.results.setItem(row, col, cell)
            overall = 'PASS' if all(result.result == 'PASS' for result in output) else 'FAIL'
            rate = sample_rate_hz(self.excited_samples)
            self.rate_label.setText(f'Estimated sample rate: {rate:.1f} Hz '
                                    f'| baseline {len(self.baseline_samples)} samples '
                                    f'| excited {len(self.excited_samples)} samples')
            saved = self.log_results(output, overall, rate)
            self.set_test_state(overall, f'{self.test_hz} Hz | saved: {saved}')
        except (ValueError, OSError) as exc:
            self.set_test_state('ERROR', str(exc))
        finally:
            self.expected_command = ''
            if self.worker and self.is_connected:
                self.worker.send('baseline')  # always request actuator OFF

    def log_results(self, results, overall: str, rate: float) -> str:
        directory = ROOT / 'runs'
        directory.mkdir(parents=True, exist_ok=True)
        suffix = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
        stem = f'test_{suffix}'
        csv_file = directory / f'{stem}.csv'
        json_file = directory / f'{stem}.json'
        first_timestamp = min(self.baseline_samples[0][0], self.excited_samples[0][0])
        with csv_file.open('w', encoding='utf-8', newline='') as handle:
            writer = csv.writer(handle)
            writer.writerow(['phase', 'relative_time_s'] + [f'sensor_{i}' for i in range(1, 9)])
            for name, samples in [('baseline', self.baseline_samples),
                                  ('excited', self.excited_samples)]:
                for timestamp, values in samples:
                    writer.writerow([name, f'{timestamp - first_timestamp:.6f}', *values])
        with json_file.open('w', encoding='utf-8') as handle:
            json.dump({
                'utc_timestamp': datetime.now(timezone.utc).isoformat(),
                'port': self.port.currentData(),
                'frequency_hz': self.test_hz,
                'minimum_response_amplitude': self.test_min_amplitude,
                'minimum_snr_db': self.test_min_snr_db,
                'estimated_sample_rate_hz': rate,
                'baseline_sample_count': len(self.baseline_samples),
                'excited_sample_count': len(self.excited_samples),
                'overall_result': overall,
                'sensors': [vars(result) for result in results],
                'raw_csv': csv_file.name,
            }, handle, indent=2)
        return str(json_file.relative_to(ROOT))

    def finish_test_error(self, reason: str):
        if self.test_state not in self.ACTIVE_STATES:
            return
        self.expected_command = ''
        self.set_test_state('ERROR', reason)
        if self.worker and self.is_connected:
            self.worker.send('baseline')

    def abort_test(self):
        if self.test_state in self.ACTIVE_STATES:
            self.expected_command = ''
            self.set_test_state('ABORTED', 'Operator aborted; requesting baseline')
            if self.worker and self.is_connected:
                self.worker.send('baseline')

    def save_settings(self):
        self.settings['default_frequency_hz'] = int(self.frequency.value())
        self.settings['min_amplitude'] = float(self.min_amplitude.value())
        self.settings['min_snr_db'] = float(self.min_snr.value())
        try:
            SETTINGS_PATH.write_text(json.dumps(self.settings, indent=2) + '\n', encoding='utf-8')
            self.statusBar().showMessage(f'Saved {SETTINGS_PATH}', 5000)
        except OSError as exc:
            QMessageBox.warning(self, 'Could not save settings', str(exc))

    def render(self):
        for curve, history in zip(self.curves, self.history):
            if history:
                latest = history[-1][0]
                recent = [(t - latest, value) for t, value in history if t >= latest - 5]
                curve.setData([t for t, _ in recent], [value for _, value in recent])

    def closeEvent(self, event):
        if self.worker:
            if self.test_state in self.ACTIVE_STATES:
                self.abort_test()
            worker = self.worker
            worker.stop()
        event.accept()


if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
