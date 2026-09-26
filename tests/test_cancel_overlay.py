import importlib.util
import os
from pathlib import Path
import signal
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication, QLabel

SOURCE = Path(__file__).resolve().parents[1] / 'src/minimal-whisper-control.py'
spec = importlib.util.spec_from_file_location('whisper_control', SOURCE)
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)


class CancelOverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.controller = control.Controller.__new__(control.Controller)
        self.controller.overlay = control.Overlay('dark')
        self.controller.window = SimpleNamespace(message=QLabel())
        self.controller.listener_running = True
        self.controller.dismissed_operation_id = None
        self.controller.overlay_operation_id = None
        self.controller.cancel_waiting = False
        self.controller.overlay.cancel_button.clicked.connect(
            self.controller.cancel_active_operation)
        self.addCleanup(self.controller.overlay.close)

    def test_cancel_hides_only_the_cancelled_run_without_waiting_for_an_idle_poll(self):
        self.controller.refresh_overlay({'state': 'transcribing', 'operation_id': 'old'})
        self.assertTrue(self.controller.overlay.isVisible())
        with tempfile.TemporaryDirectory() as directory:
            pidfile = Path(directory) / 'ptt.pid'
            pidfile.write_text('12345')
            with patch.object(control, 'PTT_PID', pidfile), \
                 patch.object(Path, 'read_bytes', return_value=b'python minimal-whisper-ptt.py'), \
                 patch.object(control.os, 'kill') as kill:
                self.controller.overlay.cancel_button.click()
                kill.assert_called_once_with(12345, signal.SIGUSR1)
        self.assertFalse(self.controller.overlay.isVisible())
        self.assertTrue(self.controller.cancel_waiting)
        for phase in ('recording', 'transcribing', 'transcribing'):
            self.controller.refresh_overlay({'state': phase, 'operation_id': 'old'})
            self.assertFalse(self.controller.overlay.isVisible())
        with patch.object(control, 'read_json', return_value={
                'state': 'recording', 'operation_id': 'new', 'waveform': [.2] * 17}):
            self.controller.refresh_waveform()
        self.assertTrue(self.controller.overlay.isVisible())
        self.assertFalse(self.controller.cancel_waiting)
        self.assertEqual(self.controller.overlay_operation_id, 'new')
        self.assertEqual(self.controller.overlay.label.text(), 'LISTENING')


if __name__ == '__main__':
    unittest.main()
