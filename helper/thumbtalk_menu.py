"""Controller navigation for a two-level radial menu, independent of the UI."""

from __future__ import annotations

import math

STICK_NEUTRAL = 0.18
STICK_SELECT = 0.28
CATEGORY_DWELL = 0.18

ROOT_ITEMS = (
    ("language", "Language"),
    ("channel", "Channels"),
    ("model", "Speech model"),
    ("sending", "Sending"),
)
SENDING_ITEMS = (("review", "Review first"), ("immediate", "Send immediately"))


class RadialNavigator:
    def __init__(self, armed=True, branches=None):
        self.branches = (
            branches
            if branches is not None
            else {"root": tuple(code for code, _ in ROOT_ITEMS)}
        )
        self.path = ["root"]
        self.page = 0
        self.hover = None
        self.armed = armed
        self.source = None
        self.pending_since = None

    @property
    def section(self):
        return self.path[-1]

    def point(self, device, x, y, items, now):
        if not math.isfinite(x) or not math.isfinite(y):
            return False
        distance = math.hypot(x, y)
        if self.source is not None and device != self.source:
            return False
        if distance < STICK_NEUTRAL:
            self.armed = True
            self.pending_since = None
            return False
        if not self.armed or distance < STICK_SELECT or not items:
            self.pending_since = None
            return False
        self.source = device
        angle = math.degrees(math.atan2(-y, x))
        index = int(math.floor(((90 - angle) % 360) / (360 / len(items)) + 0.5)) % len(
            items
        )
        choice = items[index][0]
        changed = choice != self.hover
        if changed or self.pending_since is None:
            self.pending_since = now
        self.hover = choice
        return changed

    def tick(self, now):
        if (
            self.section in self.branches
            and self.hover
            and self.pending_since is not None
            and now - self.pending_since >= CATEGORY_DWELL
        ):
            return self.enter()
        return False

    def enter(self):
        if self.hover not in self.branches.get(self.section, ()):
            return False
        self.path.append(self.hover)
        self.page = 0
        self.hover = None
        self.pending_since = None
        # Holding the same direction must not also select a child item.
        self.armed = False
        return True

    def cycle(self, direction, items):
        if not items:
            return
        codes = [code for code, label in items]
        index = (
            (codes.index(self.hover) + direction) % len(codes)
            if self.hover in codes
            else (0 if direction > 0 else -1)
        )
        self.hover = codes[index]
        self.pending_since = None  # D-pad uses Talk to enter, not an automatic timer.

    def change_page(self, direction, count):
        if count < 2:
            return False
        self.page = (self.page + direction) % count
        self.hover = None
        self.pending_since = None
        self.armed = False
        return True

    def back(self):
        if len(self.path) > 1:
            self.path.pop()
        self.page = 0
        self.hover = None
        self.pending_since = None
        self.armed = False

    def selection(self):
        return (
            (self.section, self.hover)
            if self.section not in self.branches and self.hover is not None
            else None
        )
