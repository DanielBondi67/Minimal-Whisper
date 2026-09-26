"""Prevent a held push-to-talk key from restarting after cancellation."""


class ShortcutReleaseGate:
    def __init__(self):
        self.blocked_until_release = False
        self.held = False

    def press(self):
        if not self.may_start():
            return False
        self.held = True
        return True

    def cancel_held_shortcut(self, is_pressed):
        if is_pressed:
            self.blocked_until_release = True

    def may_start(self):
        return not self.blocked_until_release and not self.held

    def release(self, physically_down=False):
        if physically_down:
            return False
        was_held = self.held
        self.held = False
        if self.blocked_until_release:
            self.blocked_until_release = False
            return False
        return was_held
