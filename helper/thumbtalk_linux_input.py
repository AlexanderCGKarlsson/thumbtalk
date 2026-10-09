"""Private ydotool keyboard and Decktation-style Linux chat transfer.

Reference: silverfoxy/decktation 32e1a7d, backend/src/wow_voice_chat.py.
Enter -> xclip(full command) -> Ctrl+V -> 300 ms grace/restore -> Enter.
No chat-style command, slash-key opener, text typing or automatic resend.
"""

from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import time


def tool_paths():
    bundle = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    directory = bundle / "input"
    if all((directory / name).is_file() for name in ("ydotool", "ydotoold")):
        return str(directory / "ydotool"), str(directory / "ydotoold")
    if not getattr(sys, "frozen", False):
        client, daemon = shutil.which("ydotool"), shutil.which("ydotoold")
        if client and daemon:
            return client, daemon
    raise RuntimeError(
        "Linux input helper is missing. Install the complete ThumbTalk package."
    )


class LinuxKeyboard:
    """Own one keyboard-only daemon; never reuse or stop another app's service."""

    def __init__(self, on_key=lambda key: None, log=lambda message: None):
        self.on_key, self.log = on_key, log
        self.process = self.directory = None
        self.closed = False

    def start(self):
        if self.closed:
            raise RuntimeError("Linux input helper is closed. Restart ThumbTalk.")
        if self.process is not None:
            if self.process.poll() is not None:
                raise RuntimeError(
                    "Linux input helper stopped. Restart ThumbTalk; no message was retried."
                )
            return
        self.client, daemon = tool_paths()
        if not os.access("/dev/uinput", os.R_OK | os.W_OK):
            raise RuntimeError(
                "Linux virtual keyboard access is unavailable (/dev/uinput). See the Linux input access section in the download instructions. Nothing has been sent."
            )
        self.directory = tempfile.TemporaryDirectory(
            prefix="thumbtalk-input-", dir="/tmp"
        )
        self.socket = str(Path(self.directory.name) / "keyboard.sock")
        self.env = dict(os.environ)
        for name in ("LD_PRELOAD", "LD_LIBRARY_PATH"):
            self.env.pop(name, None)
        self.env["YDOTOOL_SOCKET"] = self.socket
        try:
            self.process = subprocess.Popen(
                [
                    daemon,
                    "--socket-path",
                    self.socket,
                    "--socket-perm",
                    "0600",
                    "--mouse-off",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=self.env,
            )
            deadline = time.monotonic() + 3
            while not Path(self.socket).exists():
                if self.process.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError(
                        "Linux virtual keyboard could not start. Check /dev/uinput access and restart ThumbTalk."
                    )
                time.sleep(0.05)
            # This is device startup, before opening chat. Let udev enumerate it.
            time.sleep(0.3)
            if self.process.poll() is not None:
                raise RuntimeError("Linux virtual keyboard stopped during startup.")
            self.log(
                "Linux input: private ydotool virtual keyboard; Decktation send sequence"
            )
        except Exception:
            self.close()
            raise

    def keys(self, codes, expected, guard):
        from thumbtalk_platform import DeliveryStopped

        self.start()
        if not guard():
            raise DeliveryStopped(
                "Chat input interrupted. Check WoW chat before retrying."
            )
        for key in expected:
            self.on_key(key)
        try:
            result = subprocess.run(
                [self.client, "key", *codes],
                env=self.env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
            )
            if result.returncode:
                raise RuntimeError("Linux keyboard command failed; no automatic retry.")
        except Exception:
            # A timeout may have interrupted a chord between down and up.
            # Destroying our virtual device releases its keys without resending.
            self.close()
            raise

    def close(self):
        self.closed = True
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=2)
            self.process = None
        if self.directory is not None:
            self.directory.cleanup()
            self.directory = None


def send_command(text, keyboard, clipboard, guard, log):
    """Same input commands, order and paste grace as Decktation's WoW preset.

    ThumbTalk adds focus/cancel checks and clipboard replacement detection.
    These observe input; they do not lock out concurrent gameplay buttons.
    """
    from pynput.keyboard import Key

    keyboard.start()  # Check device access before opening the game's editor.
    phase, lease = "open", None
    try:
        log("open requested (Enter)")
        keyboard.keys(("28:1", "28:0"), (Key.enter,), guard)
        phase = "insert"
        lease = clipboard.acquire(text)
        clipboard.arm(lease)
        log("insert requested (whole-message paste)")
        keyboard.keys(("29:1", "47:1", "47:0", "29:0"), (Key.ctrl_l, "v"), guard)
        clipboard.wait_for_paste(lease)
        clipboard.arm(lease)
        clipboard.restore(lease)
        lease = None
        phase = "submit"
        log("submit requested (Enter)")
        keyboard.keys(("28:1", "28:0"), (Key.enter,), guard)
        log("native input completed; delivery and editor focus unconfirmed")
    except Exception:
        log(f"stopped during {phase}; no automatic retry")
        raise
    finally:
        if lease is not None:
            try:
                clipboard.restore(lease)
            except Exception:
                log("Clipboard cleanup failed; original transfer result retained")
                clipboard.close()
