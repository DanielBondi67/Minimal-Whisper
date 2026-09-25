"""Prevent a held push-to-talk key from restarting after cancellation."""


class ShortcutReleaseGate:
    def __init__(self):
        self.blocked_until_release = False

    def cancel_held_shortcut(self, is_pressed):
        if is_pressed:
            self.blocked_until_release = True

    def may_start(self):
        return not self.blocked_until_release

    def release(self, physically_down=False):
        if physically_down:
            return False
        if self.blocked_until_release:
            self.blocked_until_release = False
            return False
        return True
