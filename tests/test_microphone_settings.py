"""Microphone selection must survive autosave and reach a running listener."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication, QComboBox

spec = importlib.util.spec_from_file_location(
    'microphone_control', Path(__file__).resolve().parents[1] / 'src/minimal-whisper-control.py')
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)


class MicrophoneSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'settings.json'
        self.settings_path = patch.object(control, 'SETTINGS', self.path)
        self.settings_path.start()
        self.addCleanup(self.settings_path.stop)
        self.selector = QComboBox()
        self.selector.addItem('Disconnected headphones', 'bluez_input.headset')
        self.selector.addItem('Built-in microphone', 'alsa_input.builtin')
        self.selector.addItem('System default', '')
        saved = {**control.DEFAULTS, 'audio_source': 'bluez_input.headset'}
        # Controller.refresh_theme shares this dictionary with the window.
        self.window = SimpleNamespace(
            settings=saved, app=SimpleNamespace(
                settings=saved, refresh_theme=Mock(), refresh_status=Mock()),
            audio_source=self.selector, schedule_save=Mock(),
            audio_meter_toggle=Mock(), start_audio_meter=Mock(),
            _save_timer=Mock(), message=Mock(),
            selected_model_is_downloading=Mock(return_value=False),
            whisper_model_info={}, resume_listener_for_selected_model=Mock(return_value=False),
            update_service_controls=Mock())
        self.window.audio_meter_toggle.isChecked.return_value = False
        self.window.collect_settings = lambda: {
            **self.window.settings, 'audio_source': self.selector.currentData()}
        self.selector.currentIndexChanged.connect(
            lambda index: control.MainWindow.audio_source_changed(self.window, index))

    def save(self, running):
        with patch.object(control, 'service_running', return_value=running), \
             patch.object(control, 'service_action', return_value=
                          subprocess.CompletedProcess([], 0, '', '')) as action:
            control.MainWindow.save_settings(self.window)
        return action

    def test_selection_is_saved_and_restarts_running_listener_once(self):
        self.selector.setCurrentIndex(1)
        self.assertEqual(self.window.settings['audio_source'], 'bluez_input.headset')
        self.window.schedule_save.assert_called_once()
        action = self.save(running=True)
        action.assert_called_once_with('restart', background=True)
        self.assertEqual(json.loads(self.path.read_text())['audio_source'], 'alsa_input.builtin')
        self.assertEqual(self.window.app.settings['audio_source'], 'alsa_input.builtin')
        self.save(running=True).assert_not_called()

    def test_selecting_default_does_not_start_stopped_listener(self):
        self.selector.setCurrentIndex(2)
        self.save(running=False).assert_not_called()
        self.assertEqual(json.loads(self.path.read_text())['audio_source'], '')

    def test_failed_save_keeps_previous_selection_and_can_retry(self):
        self.selector.setCurrentIndex(1)
        with patch.object(control, 'write_settings', side_effect=OSError('disk full')):
            self.save(running=True).assert_not_called()
        self.assertEqual(self.window.settings['audio_source'], 'bluez_input.headset')
        self.save(running=True).assert_called_once_with('restart', background=True)


if __name__ == '__main__':
    unittest.main()
