"""Resources owned by exactly one recording and its transcription."""

import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import threading
import uuid


class OperationCancelled(Exception):
    pass


class DictationOperation:
    def __init__(self):
        self.id = uuid.uuid4().hex
        self.directory = Path(tempfile.mkdtemp(prefix='minimal-whisper-'))
        self.wav = self.directory / 'recording.wav'
        self.cancelled = threading.Event()
        self.waveform_stop = threading.Event()
        self.stage = 'recording'
        self.recorder = None
        self.worker = None
        self.clipboard_text = None
        self._lock = threading.Lock()
        self._processes = []

    def check(self):
        if self.cancelled.is_set():
            raise OperationCancelled()

    def spawn(self, command, **kwargs):
        # Cancellation and child registration must be atomic: cancellation must
        # either kill this child or prevent it from being started at all.
        with self._lock:
            self.check()
            process = subprocess.Popen(command, start_new_session=True, **kwargs)
            self._processes.append(process)
            return process

    def close(self):
        with self._lock:
            self.cancelled.set()
            self.waveform_stop.set()
            processes, self._processes = self._processes, []
            for process in processes:
                try:
                    # Include descendants even when the direct child exited.
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            shutil.rmtree(self.directory, ignore_errors=True)
        if processes:
            # Reap children without delaying the shortcut loop or a fresh run.
            threading.Thread(target=self._reap, args=(processes,), daemon=True).start()

    @staticmethod
    def _reap(processes):
        for process in processes:
            process.wait()
