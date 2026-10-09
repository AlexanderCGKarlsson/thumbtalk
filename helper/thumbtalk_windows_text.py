"""Windows text packets: transcript letters must not become game keybindings."""

import ctypes
import os
import time

# Identify only this process's own input; Steam and hardware events stay live.
INPUT_MARKER = int.from_bytes(os.urandom(4), "little") or 1


class KeyboardInput(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_uint16),
        ("wScan", ctypes.c_uint16),
        ("dwFlags", ctypes.c_uint32),
        ("time", ctypes.c_uint32),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_int32),
        ("dy", ctypes.c_int32),
        ("mouseData", ctypes.c_uint32),
        ("dwFlags", ctypes.c_uint32),
        ("time", ctypes.c_uint32),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class InputUnion(ctypes.Union):
    _fields_ = [("ki", KeyboardInput), ("mi", MouseInput)]


class Input(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("data", InputUnion)]


def packet(unit, released=False):
    return Input(
        1,
        InputUnion(
            ki=KeyboardInput(0, unit, 4 | (2 if released else 0), 0, INPUT_MARKER)
        ),
    )


class WindowsTextWriter:
    def __init__(self, send_input=None):
        if send_input is None:
            self.user32 = ctypes.WinDLL("user32", use_last_error=True)
            send_input = self.user32.SendInput
            send_input.argtypes = (ctypes.c_uint32, ctypes.POINTER(Input), ctypes.c_int)
            send_input.restype = ctypes.c_uint32
        self.send_input = send_input

    def character(self, character):
        self.text(character)

    def text(self, text):
        # Insert the entire draft as one ordered batch. No Ctrl+V or letter VKs.
        # Keep surrogate pairs together and never retry a partially accepted batch.
        encoded = text.encode("utf-16-le")
        if not encoded:
            return
        units = [
            int.from_bytes(encoded[i : i + 2], "little")
            for i in range(0, len(encoded), 2)
        ]
        events = (Input * (2 * len(units)))(
            *[event for unit in units for event in (packet(unit), packet(unit, True))]
        )
        accepted = self.send_input(len(events), events, ctypes.sizeof(Input))
        if accepted != len(events):
            if accepted > 0:
                # A partial batch may have left a packet down. Only release it;
                # never retry text or fall back to physical keys.
                releases = (Input * len(units))(*[packet(unit, True) for unit in units])
                self.send_input(len(releases), releases, ctypes.sizeof(Input))
            raise RuntimeError(
                "Windows could not deliver text. No keyboard fallback was used. Clear any partial draft before retrying."
            )


class WindowsControlKeys:
    """Tagged control keys for the companion's Enter and addon signals."""

    def __init__(self, writer=None):
        self.writer = writer or WindowsTextWriter()

    def _send(self, key, released):
        value = getattr(key, "value", key)
        vk = getattr(value, "vk", None)
        if vk is None:
            raise ValueError("Only explicit Windows control keys are supported.")
        event = Input(
            1,
            InputUnion(ki=KeyboardInput(vk, 0, 2 if released else 0, 0, INPUT_MARKER)),
        )
        if self.writer.send_input(1, ctypes.pointer(event), ctypes.sizeof(Input)) != 1:
            raise RuntimeError("Windows could not deliver the chat control key.")

    def press(self, key):
        self._send(key, False)

    def release(self, key):
        self._send(key, True)

    def slash(self, user32=None, sleep=time.sleep, hold=0.08):
        """Native / key in the foreground window's layout, tagged as our input."""
        from types import SimpleNamespace

        u = user32 or ctypes.WinDLL("user32", use_last_error=True)
        if user32 is None:
            u.GetForegroundWindow.restype = ctypes.c_void_p
            u.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            u.GetKeyboardLayout.argtypes = [ctypes.c_uint32]
            u.GetKeyboardLayout.restype = ctypes.c_void_p
            u.VkKeyScanExW.argtypes = [ctypes.c_wchar, ctypes.c_void_p]
            u.VkKeyScanExW.restype = ctypes.c_short
        thread = u.GetWindowThreadProcessId(u.GetForegroundWindow(), None)
        mapping = u.VkKeyScanExW("/", u.GetKeyboardLayout(thread))
        if mapping == -1 or (mapping >> 8) & ~7:
            raise ValueError(
                "The active keyboard layout cannot type / to open WoW chat."
            )
        modifiers = [
            vk for bit, vk in ((1, 0xA0), (2, 0xA2), (4, 0xA4)) if (mapping >> 8) & bit
        ]
        held = []
        try:
            for vk in modifiers + [mapping & 255]:
                key = SimpleNamespace(vk=vk)
                held.append(key)
                self.press(key)
            sleep(hold)
        finally:
            for key in reversed(held):
                self.release(key)
