"""Cancellation tokens that keep each dictation run isolated from the next."""

import threading


class OperationEpoch:
    def __init__(self):
        self.current_id = 0
        self._lock = threading.RLock()

    def begin(self):
        with self._lock:
            self.current_id += 1
            return self.current_id, threading.Event()

    def cancel(self, cancelled):
        with self._lock:
            cancelled.set()
            self.current_id += 1

    def is_current(self, operation_id, cancelled):
        with self._lock:
            return self.current_id == operation_id and not cancelled.is_set()

    def run_if_current(self, operation_id, cancelled, action):
        """Run a short side effect atomically against begin/cancel."""
        with self._lock:
            if self.current_id != operation_id or cancelled.is_set():
                return False
            action()
            return self.current_id == operation_id and not cancelled.is_set()
