"""Track controls without treating handheld stick movement as a typing conflict."""

import threading
import time


class InputActivity:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.RLock()
        self.held = set()
        self.axes = set()
        self.changed = clock()
        self.version = 0
        self.button_version = 0
        self.buttons_changed = self.changed

    def button(self, kind, device, name, down):
        with self.lock:
            identity = (kind, device, name)
            was = identity in self.held
            self.held.add(identity) if down else self.held.discard(identity)
            if was != down:
                self.changed = self.clock()
                self.version += 1
                # WoW can interpret native pad triggers/shoulders as modifiers
                # or chat UI actions without an OS keyboard event. Count every
                # button, while leaving analog axes out of the text guard.
                self.button_version += 1
                self.buttons_changed = self.changed

    def axis(self, kind, device, name, value):
        with self.lock:
            identity = (kind, device, name)
            was = identity in self.axes
            active = abs(value) > (0.12 if was else 0.20)
            self.axes.add(identity) if active else self.axes.discard(identity)
            if was != active:
                self.changed = self.clock()
                self.version += 1

    def disconnect(self, device):
        with self.lock:
            self.held = {
                item
                for item in self.held
                if item[0] not in ("pad", "raw") or item[1] != device
            }
            self.axes = {item for item in self.axes if item[1] != device}
            self.changed = self.clock()
            self.version += 1
            self.button_version += 1
            self.buttons_changed = self.changed

    def text_ready(self, version=None):
        """Wait for buttons, not analog movement; never suppress gameplay input."""
        with self.lock:
            return (
                not self.held
                and self.clock() - self.buttons_changed >= 0.20
                and (version is None or version == self.button_version)
            )

    def quiet(self, version=None):
        with self.lock:
            return (
                not self.held
                and not self.axes
                and self.clock() - self.changed >= 0.20
                and (version is None or version == self.version)
            )
