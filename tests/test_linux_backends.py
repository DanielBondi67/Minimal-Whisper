import subprocess
import unittest
from unittest.mock import patch

from src.audio_backend import AudioBackend, PipeWireBackend, PulseAudioBackend
from src.text_output import normalize_transcription
from src.wayland_portal import PortalError, _portal_trigger


class AudioBackendTests(unittest.TestCase):
    def test_pipewire_capture_uses_selected_node_and_whisper_format(self):
        command = PipeWireBackend().recording_command(
            '47', '/tmp/input.wav', rate=16000, channels=1)
        self.assertEqual(command, ['pw-record', '--target', '47', '--rate', '16000',
                                   '--channels', '1', '--format', 's16',
                                   '/tmp/input.wav'])

    def test_pulse_capture_writes_a_wav_file(self):
        command = PulseAudioBackend().recording_command(
            'alsa_input.example', '/tmp/input.wav')
        self.assertIn('--file-format=wav', command)
        self.assertIn('--device', command)
        self.assertEqual(command[-1], '/tmp/input.wav')

    @patch('src.audio_backend.shutil.which')
    @patch('src.audio_backend.subprocess.run')
    def test_detects_pipewire_from_pulse_compatibility_server(self, run, which):
        which.side_effect = lambda name: f'/usr/bin/{name}' if name in {
            'pactl', 'pw-record'} else None
        run.return_value = subprocess.CompletedProcess(
            ['pactl'], 0, 'Server Name: PulseAudio (on PipeWire 1.2.0)\n', '')
        self.assertIsInstance(AudioBackend.detect(), PipeWireBackend)

    @patch('src.audio_backend.shutil.which')
    @patch('src.audio_backend.subprocess.run')
    def test_detects_pulseaudio_without_pactl(self, run, which):
        which.side_effect = lambda name: f'/usr/bin/{name}' if name == 'parecord' else None
        run.return_value = subprocess.CompletedProcess(
            ['wpctl'], 1, '', 'not a PipeWire session')
        self.assertIsInstance(AudioBackend.detect(), PulseAudioBackend)


class PortalShortcutTests(unittest.TestCase):
    def test_converts_german_layout_super_control_shortcut(self):
        self.assertEqual(_portal_trigger('Meta+Ctrl+Y'), 'SUPER+CTRL+y')

    def test_rejects_shortcut_without_modifier(self):
        with self.assertRaises(PortalError):
            _portal_trigger('y')


class TextOutputTests(unittest.TestCase):
    def test_line_breaks_do_not_become_keyboard_return_events(self):
        self.assertEqual(normalize_transcription(
            'First sentence.\r\nSecond sentence.\n'),
            'First sentence. Second sentence.')

    def test_unicode_paragraph_separators_are_replaced(self):
        self.assertEqual(normalize_transcription('one\u2028two\u2029three'),
                         'one two three')


if __name__ == '__main__':
    unittest.main()
