"""Serial worker tests using a simulated port; no hardware is accessed."""
import os
import queue
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication
import serial

from main import SerialWorker


class FakeSerial:
    def __init__(self):
        self.incoming = queue.Queue()
        self.writes = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def reset_input_buffer(self):
        pass

    def write(self, data):
        self.writes.append(data)
        return len(data)

    def flush(self):
        pass

    def readline(self):
        try:
            return self.incoming.get(timeout=0.02)
        except queue.Empty:
            return b''


class TestSerialWorker(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait_for(self, condition, timeout=3):
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            self.app.processEvents()
            if condition():
                return True
            time.sleep(0.005)
        return False

    def test_stream_command_and_shutdown_use_expected_protocol(self):
        port = FakeSerial()
        worker = SerialWorker('FAKE')
        connections, samples, messages, malformed, commands = [], [], [], [], []
        worker.connected.connect(connections.append)
        worker.sample.connect(lambda timestamp, values: samples.append(values))
        worker.device_message.connect(lambda timestamp, message: messages.append(message))
        worker.malformed.connect(malformed.append)
        worker.command_sent.connect(lambda command, timestamp: commands.append(command))
        with patch('main.serial.Serial', return_value=port):
            worker.start()
            try:
                self.assertTrue(self.wait_for(lambda: True in connections), 'Worker did not connect')
                worker.send('response:10')
                port.incoming.put(b'1, 2, 3, 4, 5, 6, 7, 8,\r\n')
                port.incoming.put(b'10.00\r\n')
                port.incoming.put(b'1, 2, 3, 4, 5, 6, 7, broken\r\n')
                self.assertTrue(self.wait_for(lambda: samples and messages and malformed and commands),
                                'Worker did not deliver the expected messages')
                self.assertEqual(samples, [(1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0)])
                self.assertEqual(messages, ['10.00'])
                self.assertEqual(commands, ['response:10'])
            finally:
                worker.stop()
                self.app.processEvents()
        self.assertEqual(port.writes, [b'response:10', b'baseline'])
        self.assertIn(False, connections)

    def test_open_failure_reports_error_and_disconnect(self):
        worker = SerialWorker('FAKE')
        statuses, connections = [], []
        worker.status.connect(statuses.append)
        worker.connected.connect(connections.append)
        with patch('main.serial.Serial', side_effect=serial.SerialException('port unavailable')):
            worker.run()
        self.assertTrue(any('port unavailable' in status for status in statuses))
        self.assertEqual(connections, [False])


if __name__ == '__main__':
    unittest.main()
