"""Short-lived addon camera-lock lease; no controller grabs or saved CVar changes."""

import time


class MenuCameraLock:
    def __init__(self, current_target, signal, clock=time.monotonic):
        self.current_target, self.signal, self.clock = current_target, signal, clock
        self.target = None
        self.next_refresh = 0
        self.error = None

    def update(self, active):
        if not active:
            self.close()
            return
        now = self.clock()
        if now < self.next_refresh:
            return
        self.next_refresh = now + 0.35
        target = self.current_target()
        if target is None:
            self.target = None  # The addon expires its lease if focus is lost.
            return
        try:
            self.signal(True, lambda: self.current_target() == target)
            self.target = target
            self.error = None
        except Exception as error:
            if str(error) != self.error:
                print("Menu camera lock: " + str(error), flush=True)
                self.error = str(error)

    def close(self):
        target, self.target = self.target, None
        self.next_refresh = 0
        if target is not None and self.current_target() == target:
            try:
                self.signal(False, lambda: self.current_target() == target)
            except Exception:
                pass  # Addon timeout restores input even if release cannot arrive.
