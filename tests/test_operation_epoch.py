import unittest

from src.operation_epoch import OperationEpoch


class OperationEpochTests(unittest.TestCase):
    def test_cancelled_operation_stays_invalid_after_a_new_run_starts(self):
        epoch = OperationEpoch()
        old_id, old_cancelled = epoch.begin()
        self.assertTrue(epoch.is_current(old_id, old_cancelled))

        epoch.cancel(old_cancelled)
        new_id, new_cancelled = epoch.begin()

        self.assertFalse(epoch.is_current(old_id, old_cancelled))
        self.assertTrue(old_cancelled.is_set())
        self.assertTrue(epoch.is_current(new_id, new_cancelled))
        self.assertFalse(new_cancelled.is_set())


if __name__ == '__main__':
    unittest.main()
