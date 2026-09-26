"""Exercise cancellation with real child processes, without a mic or desktop."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / 'src/minimal-whisper-ptt.py'
spec = importlib.util.spec_from_file_location('dictation_listener', SOURCE)
listener = importlib.util.module_from_spec(spec)
spec.loader.exec_module(listener)

RECORDER = '''
import signal, sys, time, wave
signal.signal(signal.SIGINT, signal.SIG_IGN if sys.argv[2] == 'ignore' else
              lambda *args: sys.exit(0))
with wave.open(sys.argv[1], 'wb') as wav:
    wav.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
    wav.writeframes(bytes(32000))
while True:
    time.sleep(.01)
'''

TRANSCRIBER = '''
import json, signal, subprocess, sys, time
from pathlib import Path
signal.signal(signal.SIGTERM, signal.SIG_IGN)
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
Path(sys.argv[3]).write_text(json.dumps([__import__('os').getpid(), child.pid]))
while not Path(sys.argv[2]).exists():
    time.sleep(.01)
Path(sys.argv[1]).with_suffix('.txt').write_text(sys.argv[4])
child.kill()
child.wait()
'''


def wait_until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError('Timed out waiting for child process')


def running(pid):
    try:
        return Path(f'/proc/{pid}/stat').read_text().split(') ', 1)[1][0] != 'Z'
    except FileNotFoundError:
        return False


class DictationCancellationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [
            patch.object(listener, 'LOG', self.root / 'log'),
            patch.object(listener, 'STATE', self.root / 'status.json'),
            patch.object(listener.AudioBackend, 'detect'),
            patch.object(listener, 'insert_transcription', return_value='test delivery'),
        ]
        self.mocks = [p.start() for p in self.patches]
        self.addCleanup(self.temp.cleanup)
        for p in self.patches:
            self.addCleanup(p.stop)
        self.ignore_interrupt = False
        self.mocks[2].return_value.recording_command.side_effect = (
            lambda source, path, rate, channels: [sys.executable, '-c', RECORDER,
                str(path), 'ignore' if self.ignore_interrupt else 'exit'])
        self.delivery = self.mocks[3]
        self.dictation = listener.Dictation()
        self.addCleanup(self.dictation.cancel)
        self.dictation.verify_model_is_downloaded = lambda op: 'fake-model'
        self.dictation._transcription_command = lambda op, model: [
            sys.executable, '-c', TRANSCRIBER, str(op.wav),
            str(self.root / f'{op.id}.go'), str(self.root / f'{op.id}.started'), op.id]

    def record(self):
        self.dictation.start()
        op = self.dictation.active
        wait_until(lambda: op.wav.exists() and op.wav.stat().st_size > 2048)
        return op

    def transcription_started(self, op):
        marker = self.root / f'{op.id}.started'
        wait_until(marker.exists)
        return json.loads(marker.read_text())

    def test_cancel_recording_discards_files_and_allows_immediate_restart(self):
        old = self.record()
        self.dictation.cancel_current()
        self.assertFalse(old.directory.exists())
        self.assertIsNone(self.dictation.active)
        self.dictation.stop_and_transcribe()  # releasing the cancelled shortcut
        new = self.record()
        self.assertNotEqual(old.id, new.id)
        wait_until(lambda: old.recorder.poll() is not None)
        self.dictation.poll()
        self.delivery.assert_not_called()
        self.assertEqual(json.loads(listener.STATE.read_text())['operation_id'], new.id)

    def test_cancel_transcription_kills_descendants_and_next_run_delivers_only_new_text(self):
        old = self.record()
        start = time.monotonic()
        self.dictation.stop_and_transcribe()
        self.assertLess(time.monotonic() - start, .5, 'Input loop blocked on inference')
        pids = self.transcription_started(old)
        self.dictation.cancel_pending.set()  # same request as SIGUSR1 handler
        self.dictation.poll()
        self.assertFalse(old.directory.exists())
        new = self.record()
        wait_until(lambda: all(not running(pid) for pid in pids))
        old.worker.join(timeout=2)
        self.assertFalse(old.worker.is_alive())
        self.dictation._complete(old, text='late stale result')
        self.dictation.poll()
        self.delivery.assert_not_called()
        self.assertIs(self.dictation.active, new)

        (self.root / f'{new.id}.go').touch()
        self.dictation.stop_and_transcribe()
        wait_until(lambda: not self.dictation._completed.empty())
        self.dictation.poll()
        self.delivery.assert_called_once_with(new.id, self.dictation, new)
        self.assertFalse(new.directory.exists())

    def test_cancel_after_inference_finishes_but_before_delivery_drops_queued_result(self):
        op = self.record()
        (self.root / f'{op.id}.go').touch()
        self.dictation.stop_and_transcribe()
        wait_until(lambda: not self.dictation._completed.empty())
        self.dictation.cancel_pending.set()
        self.dictation.poll()
        self.delivery.assert_not_called()
        self.assertTrue(self.dictation._completed.empty())
        self.assertFalse(op.directory.exists())

    def test_cancel_while_recorder_is_stopping_still_kills_capture(self):
        self.ignore_interrupt = True
        op = self.record()
        self.dictation.stop_and_transcribe()
        self.dictation.cancel_current()
        self.assertFalse(op.directory.exists())
        wait_until(lambda: op.recorder.poll() is not None)
        op.worker.join(timeout=2)
        self.assertFalse(op.worker.is_alive())
        self.dictation.poll()
        self.delivery.assert_not_called()

    def test_no_child_can_be_spawned_after_cancellation(self):
        op = self.record()
        self.dictation.cancel_current()
        with self.assertRaises(listener.OperationCancelled):
            op.spawn([sys.executable, '-c', 'raise AssertionError("must not run")'])

    def test_repeated_presses_during_transcription_never_start_another_capture(self):
        op = self.record()
        self.dictation.stop_and_transcribe()
        self.transcription_started(op)
        for _ in range(25):
            self.dictation.start()
        self.assertIs(self.dictation.active, op)
        self.assertEqual(self.mocks[2].return_value.recording_command.call_count, 1)

    def test_late_delivery_return_cannot_cancel_a_new_recording(self):
        old = self.record()
        self.dictation._complete(old, text='old text')

        def cancel_and_start_during_nested_portal_loop(*args):
            self.dictation.cancel_pending.set()
            self.dictation.poll()
            self.dictation.start()
            return None

        self.delivery.side_effect = cancel_and_start_during_nested_portal_loop
        self.dictation.poll()
        self.assertIsNotNone(self.dictation.active)
        self.assertNotEqual(self.dictation.active.id, old.id)
        self.assertFalse(self.dictation.active.cancelled.is_set())
        self.assertEqual(json.loads(listener.STATE.read_text())['state'], 'recording')


if __name__ == '__main__':
    unittest.main()
