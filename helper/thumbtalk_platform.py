"""Foreground-window checks and guarded keyboard delivery for Windows, macOS, and X11/XWayland."""

from __future__ import annotations

import ctypes
import math
import os
import platform
import time
import threading
from dataclasses import dataclass
from pathlib import Path

LOCAL_REVIEW_PAUSED = (
    "Review is temporarily unavailable on SteamOS/Linux after reported WoW freezes. "
    "Nothing sent. Choose Sending → Send immediately to continue."
)

WOW_EXECUTABLES = {
    "wow.exe",
    "wowclassic.exe",
    "wowclassicb.exe",
    "wowb.exe",
    "wowt.exe",
    "wow-64.exe",
}


def is_wow_class(value):
    return isinstance(value, str) and value.lower() in WOW_EXECUTABLES | {
        "wow",
        "world of warcraft",
    }


def process_executable(pid):
    """Read only the executable argument, never search arbitrary process arguments."""
    try:
        with (Path("/proc") / str(pid) / "cmdline").open("rb") as stream:
            arguments = stream.read(8192).split(b"\0")

        def basename(value):
            return os.fsdecode(value).replace("\\", "/").rsplit("/", 1)[-1].casefold()

        executable = basename(arguments[0])
        # Some Wine builds keep the loader as argv[0]; ordinary apps with a WoW
        # filename elsewhere on their command line must never count as WoW.
        if executable in {"wine", "wine64", "wine-preloader", "wine64-preloader"}:
            executable = basename(arguments[1]) if len(arguments) > 1 else ""
        return executable
    except (OSError, ValueError):
        return ""


@dataclass(frozen=True)
class Target:
    system: str
    window: int
    process: int = 0


class Foreground:
    def __init__(self):
        self.lock = threading.RLock()
        self.system = platform.system()
        self.display = None
        self.last_focus_detail = None
        if self.system == "Windows":
            from ctypes import wintypes

            self.user = ctypes.WinDLL("user32", use_last_error=True)
            self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            self.user.GetForegroundWindow.restype = wintypes.HWND
            self.user.GetWindowThreadProcessId.argtypes = [
                wintypes.HWND,
                ctypes.POINTER(wintypes.DWORD),
            ]
            self.kernel.OpenProcess.argtypes = [
                wintypes.DWORD,
                wintypes.BOOL,
                wintypes.DWORD,
            ]
            self.kernel.OpenProcess.restype = wintypes.HANDLE
            self.kernel.QueryFullProcessImageNameW.argtypes = [
                wintypes.HANDLE,
                wintypes.DWORD,
                wintypes.LPWSTR,
                ctypes.POINTER(wintypes.DWORD),
            ]
            self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        elif self.system == "Darwin":
            from AppKit import NSWorkspace
            import Quartz

            self.workspace = NSWorkspace.sharedWorkspace()
            self.quartz = Quartz
        elif self.system == "Linux":
            if not os.environ.get("DISPLAY"):
                raise RuntimeError(
                    "No game display found. Start ThumbTalk with WoW using the Steam launch option in Setup."
                )
            from Xlib.display import Display

            self.display = Display()
        else:
            raise RuntimeError(
                "This platform can use settings and file transcription only."
            )

    def current(self):
        with self.lock:
            return self._current()

    def _current(self):
        try:
            if self.system == "Windows":
                from ctypes import wintypes

                hwnd = self.user.GetForegroundWindow()
                if not hwnd:
                    return None
                pid = wintypes.DWORD()
                self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                handle = self.kernel.OpenProcess(0x1000, False, pid.value)
                if not handle:
                    return None
                try:
                    size = wintypes.DWORD(32768)
                    value = ctypes.create_unicode_buffer(size.value)
                    if not self.kernel.QueryFullProcessImageNameW(
                        handle, 0, value, ctypes.byref(size)
                    ):
                        return None
                    executable = (
                        value.value.replace("\\", "/").rsplit("/", 1)[-1].lower()
                    )
                    return (
                        Target(self.system, int(hwnd), pid.value)
                        if executable in WOW_EXECUTABLES
                        else None
                    )
                finally:
                    self.kernel.CloseHandle(handle)
            if self.system == "Darwin":
                app = self.workspace.frontmostApplication()
                if app is None or app.executableURL() is None:
                    return None
                executable = str(app.executableURL().lastPathComponent()).casefold()
                if executable not in {
                    "wow",
                    "world of warcraft",
                    "world of warcraft classic",
                    "world of warcraft beta",
                }:
                    return None
                pid = int(app.processIdentifier())
                q = self.quartz
                windows = (
                    q.CGWindowListCopyWindowInfo(
                        q.kCGWindowListOptionOnScreenOnly
                        | q.kCGWindowListExcludeDesktopElements,
                        q.kCGNullWindowID,
                    )
                    or []
                )
                for item in windows:
                    if (
                        item.get(q.kCGWindowOwnerPID) == pid
                        and item.get(q.kCGWindowLayer) == 0
                    ):
                        return Target(self.system, int(item[q.kCGWindowNumber]), pid)
                return None
            return self._linux_current()
        except Exception:
            return None
        return None

    def _window_pid(self, window):
        # Prefer the X server's PID: Proton can run inside a PID namespace, so
        # the window's _NET_WM_PID property need not be a host PID.
        try:
            if self.display.has_extension("X-Resource"):
                reply = self.display.res_query_client_ids(
                    [{"client": window.id, "mask": 2}]
                )
                for identity in reply.ids:
                    if identity.spec.mask & 2 and len(identity.value):
                        pid = int(identity.value[0])
                        if 0 < pid < 0xFFFFFFFF:
                            return pid
        except Exception:
            pass  # Older X servers may not implement XRes 1.2.
        from Xlib import Xatom

        atom = self.display.intern_atom("_NET_WM_PID", only_if_exists=True)
        prop = window.get_full_property(atom, Xatom.CARDINAL) if atom else None
        if prop is not None and prop.format == 32 and len(prop.value) == 1:
            pid = int(prop.value[0])
            if 0 < pid < 0xFFFFFFFF:
                return pid
        return 0

    def _focus_result(self, target, detail):
        if detail != self.last_focus_detail:
            print("Game focus: " + detail, flush=True)
            self.last_focus_detail = detail
        return target

    def _linux_current(self):
        window = self.display.get_input_focus().focus
        details = []
        root = self.display.screen().root.id
        for _ in range(12):
            if not hasattr(window, "get_wm_class") or window.id == root:
                break
            classes = window.get_wm_class() or ()
            if any(is_wow_class(value) for value in classes):
                return self._focus_result(
                    Target(self.system, window.id),
                    f"WoW window {window.id:#x} (WoW class)",
                )
            pid = self._window_pid(window)
            executable = process_executable(pid) if pid else ""
            if executable in WOW_EXECUTABLES:
                return self._focus_result(
                    Target(self.system, window.id, pid),
                    f"WoW window {window.id:#x}; process={pid}; executable={executable}",
                )
            details.append(
                f"window={window.id:#x}; class={classes!r}; pid={pid}; executable={executable or 'unknown'}"
            )
            parent = window.query_tree().parent
            if parent.id == window.id:
                break
            window = parent
        return self._focus_result(
            None, "not WoW; " + (" | ".join(details) or "no focused application window")
        )

    def close(self):
        if self.display is not None:
            self.display.close()


class DeliveryStopped(RuntimeError):
    pass


@dataclass(frozen=True)
class ChatTiming:
    """Native-input pacing; overrides are for controlled device comparisons."""

    # Retain at least one 20 FPS frame for each phase; avoid per-character waits.
    open_delay: float = 0.05
    insert_delay: float = 0.05
    settle_delay: float = 0.05
    key_hold: float = 0.06

    def __post_init__(self):
        for name, value in vars(self).items():
            maximum = 0.20 if name == "key_hold" else 1.0
            if not math.isfinite(value) or not 0.05 <= value <= maximum:
                raise ValueError(
                    f"Chat timing {name} must be between 0.05 and {maximum} seconds."
                )

    @classmethod
    def from_environment(cls):
        # Clipboard transfer is asynchronous on X11/macOS. Retain its proven
        # settling time; a short key dispatch does not mean paste has completed.
        values = {"insert_delay": 0.05 if platform.system() == "Windows" else 0.20}
        for name in cls.__dataclass_fields__:
            key = "THUMBTALK_CHAT_" + name.upper()
            if key in os.environ:
                try:
                    values[name] = float(os.environ[key])
                except ValueError:
                    raise ValueError(f"{key} must be a number of seconds.") from None
        return cls(**values)


class KeyboardDelivery:
    def __init__(
        self,
        keyboard=None,
        sleep=time.sleep,
        on_key=lambda key: None,
        clipboard=None,
        timing=None,
        log=lambda message: None,
    ):
        self.timing = timing or ChatTiming.from_environment()
        self.log = log
        self.command_number = 0
        if platform.system() == "Linux":
            self.log(
                "Linux chat: Enter → xclip → ydotool Ctrl+V → 300 ms grace → Enter"
            )
        else:
            self.log(
                f"Chat timing: open={self.timing.open_delay:.2f}s "
                f"insert={self.timing.insert_delay:.2f}s "
                f"settle={self.timing.settle_delay:.2f}s hold={self.timing.key_hold:.2f}s"
            )
        native_linux = keyboard is None and platform.system() == "Linux"
        if keyboard is None:
            if platform.system() == "Windows":
                from thumbtalk_windows_text import WindowsControlKeys

                keyboard = WindowsControlKeys()
            else:
                from pynput.keyboard import Controller

                keyboard = Controller()
        self.keyboard, self.sleep, self.on_key = keyboard, sleep, on_key
        self.text_writer = None
        self.clipboard = clipboard
        self.pending_text = None
        self.linux_input = None
        if native_linux:
            self._linux_keyboard().start()

    def _linux_keyboard(self):
        if self.linux_input is None:
            from thumbtalk_linux_input import LinuxKeyboard

            self.linux_input = LinuxKeyboard(on_key=self.on_key, log=self.log)
        return self.linux_input

    def prepare_chat(self, target, classic_chat, guard):
        """Compatibility no-op: never change WoW's chat style.

        Older saved settings can retain classic_chat=True. The former style
        command was reported to freeze WoW; no setting enables it anymore.
        """
        return None

    def _tap(self, key, guard):
        if not guard():
            raise DeliveryStopped(
                "Typing stopped: focus changed or Cancel was pressed. Clear partial chat text before retrying."
            )
        self.on_key(key)
        try:
            self.keyboard.press(key)
        finally:
            self.keyboard.release(key)

    def _type(self, text, guard):
        if platform.system() == "Windows" and self.text_writer is None:
            from thumbtalk_windows_text import WindowsTextWriter

            self.text_writer = WindowsTextWriter()
        for character in text:
            if self.text_writer is not None:
                if not guard():
                    raise DeliveryStopped(
                        "Text entry stopped because focus or controls changed. Clear any partial draft before retrying."
                    )
                self.on_key(character)
                if ord(character) > 0xFFFF:
                    encoded = character.encode("utf-16-le")
                    for offset in (0, 2):
                        self.on_key(
                            chr(int.from_bytes(encoded[offset : offset + 2], "little"))
                        )
                self.text_writer.character(character)
            else:
                self._tap(character, guard)
            # Give the Windows target time to dispatch text packets, including
            # surrogate pairs, before queueing the next character. The guard
            # is checked again before each packet if player input resumes.
            self.sleep(0.03 if self.text_writer is not None else 0.002)

    def menu_signal(self, active, guard):
        from pynput.keyboard import Key

        linux = platform.system() == "Linux"
        # Use ordinary function keys consistently with the tested Linux path.
        # Keep F13/F14 accepted by the addon for older companions.
        key = Key.f11 if active else Key.f12
        if linux:
            code = "87" if active else "88"
            self._linux_keyboard().keys((code + ":1", code + ":0"), (key,), guard)
            return
        self._tap(key, guard)

    def _chat_key(self, key, guard):
        # Games may sample keyboard state once per frame. Keep control keys
        # down briefly; always release them even if delivery is interrupted.
        if not guard():
            raise DeliveryStopped(
                "Chat action stopped because focus or controls changed."
            )
        self.on_key(key)
        try:
            self.keyboard.press(key)
            self.sleep(self.timing.key_hold)
        finally:
            self.keyboard.release(key)

    def _review_signal(self, key, guard):
        from pynput.keyboard import Key

        if platform.system() == "Linux":
            code = "67" if key == Key.f9 else "68"
            self._linux_keyboard().keys(
                ("29:1", "42:1", code + ":1", code + ":0", "42:0", "29:0"),
                (Key.ctrl_l, Key.shift_l, key),
                guard,
            )
            return
        held = []
        try:
            for modifier in (Key.ctrl_l, Key.shift_l):
                if not guard():
                    raise DeliveryStopped(
                        "Chat action stopped because focus or controls changed."
                    )
                self.on_key(modifier)
                held.append(modifier)
                self.keyboard.press(modifier)
            self._chat_key(key, guard)
        finally:
            for modifier in reversed(held):
                self.keyboard.release(modifier)
        self.sleep(self.timing.settle_delay)

    @staticmethod
    def _validate_text(text):
        if (
            not text.startswith("/")
            or len(text.encode("utf-8")) > 255
            or any(ord(char) < 32 or ord(char) == 127 for char in text)
        ):
            raise ValueError("Invalid or oversized chat message.")

    def _open_slash(self, guard):
        # Unlike Enter, the normal slash chat binding also works when the
        # gamepad UI retained an empty focused editor after native submission.
        # Keep WoW's default / (Open Chat Slash) binding enabled.
        from pynput.keyboard import Key

        if platform.system() == "Windows":
            if not guard():
                raise DeliveryStopped(
                    "Chat action stopped because focus or controls changed."
                )
            self.on_key("/")
            self.keyboard.slash(sleep=self.sleep, hold=self.timing.key_hold)
        else:
            # pynput may synthesize Shift to produce / on non-US layouts.
            self.on_key(Key.shift_l)
            self._chat_key("/", guard)

    def _command(self, text, guard):
        """Run one native slash command; never call an addon Enter handler."""
        from pynput.keyboard import Key

        self._validate_text(text)
        if platform.system() == "Linux":
            from thumbtalk_linux_input import send_command
            from thumbtalk_xclip import XclipClipboard

            if (
                self.clipboard is None
                or getattr(self.clipboard, "closed", False) is True
            ):
                self.clipboard = XclipClipboard(log=self.log)
            self.command_number += 1
            number = self.command_number
            return send_command(
                text,
                self._linux_keyboard(),
                self.clipboard,
                guard,
                lambda message: self.log(f"Chat command {number}: {message}"),
            )
        windows = platform.system() == "Windows"
        lease = None
        if windows:
            if self.text_writer is None:
                from thumbtalk_windows_text import WindowsTextWriter

                self.text_writer = WindowsTextWriter()
        else:
            if (
                self.clipboard is None
                or getattr(self.clipboard, "closed", False) is True
            ):
                from thumbtalk_clipboard import ClipboardLease

                self.clipboard = ClipboardLease()
            lease = self.clipboard.acquire(text[1:])
        self.command_number += 1
        number = self.command_number
        phase = "open"
        try:
            self.log(f"Chat command {number}: open requested")
            self._open_slash(guard)
            self.sleep(self.timing.open_delay)
            phase = "insert"
            self.log(f"Chat command {number}: insert requested")
            if windows:
                for character in text[1:]:
                    self.on_key(character)
                    if ord(character) > 0xFFFF:
                        encoded = character.encode("utf-16-le")
                        for offset in (0, 2):
                            self.on_key(
                                chr(
                                    int.from_bytes(
                                        encoded[offset : offset + 2], "little"
                                    )
                                )
                            )
                if not guard():
                    raise DeliveryStopped(
                        "Text insertion stopped because focus or controls changed."
                    )
                self.text_writer.text(text[1:])
            else:
                self._paste(guard)
            self.sleep(self.timing.insert_delay)
            phase = "submit"
            self.log(f"Chat command {number}: submit requested")
            self._chat_key(Key.enter, guard)
            self.sleep(self.timing.settle_delay)
            self.log(
                f"Chat command {number}: native input completed; delivery and editor focus unconfirmed"
            )
        except Exception:
            self.log(
                f"Chat command {number}: stopped during {phase}; no automatic retry"
            )
            if lease is not None:
                try:
                    self.clipboard.restore(lease)
                except Exception:
                    self.log("Clipboard cleanup also failed; original error retained")
                    self.clipboard.close()
            raise
        else:
            if lease is not None:
                self.clipboard.restore(lease)

    @property
    def local_review_available(self):
        return True  # Linux uses the addon inbox; it never enters /ttr in chat.

    def draft(self, text, guard):
        """Display local-only history review; keep the real payload here."""
        import secrets

        if not self.local_review_available:
            raise ValueError(LOCAL_REVIEW_PAUSED)
        self._validate_text(text)
        self.pending_text = None
        if platform.system() == "Linux":
            from thumbtalk_review import transfer
            from thumbtalk_xclip import XclipClipboard

            if (
                self.clipboard is None
                or getattr(self.clipboard, "closed", False) is True
            ):
                self.clipboard = XclipClipboard(log=self.log)
            transfer(text, self._linux_keyboard(), self.clipboard, guard, self.log)
            self.pending_text = text
            return
        encoded = text.encode("utf-8").hex()
        chunks = [encoded[i : i + 200] for i in range(0, len(encoded), 200)]
        identifier = secrets.token_hex(4)
        for number, chunk in enumerate(chunks, 1):
            self._command(f"/ttr {identifier} {number} {len(chunks)} {chunk}", guard)
        self.pending_text = text

    def _paste(self, guard):
        from pynput.keyboard import Key

        modifier = Key.cmd if platform.system() == "Darwin" else Key.ctrl_l
        if not guard():
            raise DeliveryStopped("Paste stopped because game focus changed.")
        self.on_key(modifier)
        try:
            self.keyboard.press(modifier)
            self._chat_key("v", guard)
        finally:
            self.keyboard.release(modifier)

    def close(self):
        if self.linux_input is not None:
            self.linux_input.close()
        if self.clipboard is not None:
            self.clipboard.close()

    def send(self, guard, text=None):
        from pynput.keyboard import Key

        payload = text if text is not None else self.pending_text
        if payload is None:
            raise ValueError("No pending message to send.")
        self._validate_text(payload)
        # Clear before attempting input: interrupted sends are never retried.
        had_review = self.pending_text is not None
        self.pending_text = None
        self._command(payload, guard)
        # Native submission owns editor cleanup. Opening / again after Enter
        # can recreate the very chat prompt we are trying to dismiss. Do not
        # guess at editor state with an extra slash or Escape.
        if had_review or platform.system() != "Linux":
            if platform.system() == "Linux":
                self.sleep(self.timing.settle_delay)
            self._review_signal(Key.f9, guard)

    def cancel(self, guard):
        from pynput.keyboard import Key

        self.pending_text = None
        # Only remove our local history line. No editor text or focus is touched.
        self._review_signal(Key.f10, guard)
