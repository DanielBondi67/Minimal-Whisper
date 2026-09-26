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

    def test_cancelled_operation_cannot_run_late_side_effects(self):
        epoch = OperationEpoch()
        operation_id, cancelled = epoch.begin()
        epoch.cancel(cancelled)
        epoch.begin()

        effects = []
        ran = epoch.run_if_current(
            operation_id, cancelled, lambda: effects.append('stale transcription'))

        self.assertFalse(ran)
        self.assertEqual(effects, [])

    def test_cancel_during_side_effect_invalidates_operation_on_return(self):
        epoch = OperationEpoch()
        operation_id, cancelled = epoch.begin()

        ran = epoch.run_if_current(
            operation_id, cancelled, lambda: epoch.cancel(cancelled))

        self.assertFalse(ran)
        self.assertFalse(epoch.is_current(operation_id, cancelled))


if __name__ == '__main__':
    unittest.main()
