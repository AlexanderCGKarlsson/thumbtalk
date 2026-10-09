"""Linux plain-text clipboard provider using xclip, independent of Qt's event loop.

Design reference: silverfoxy/decktation's clipboard_injection.py (MIT).
This implementation retains ThumbTalk's explicit lease and interruption checks.
A grace period is not a game acknowledgement. No input is retried here.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

PASTE_GRACE_SECONDS = 0.3
CLIPBOARD_TIMEOUT_SECONDS = 3
TEXT_TARGETS = {
    b"UTF8_STRING",
    b"STRING",
    b"TEXT",
    b"COMPOUND_TEXT",
    b"text/plain",
    b"text/plain;charset=utf-8",
}


def xclip_command():
    bundle = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    bundled = bundle / "clipboard" / "xclip"
    env = dict(os.environ)
    # DISPLAY is selected for the game by ThumbTalk's launcher. Do not switch
    # to Qt's separate overlay display or guess a UID/display number here.
    if not env.get("DISPLAY"):
        raise RuntimeError("No game X11 display is available for clipboard paste.")
    if bundled.is_file():
        env["LD_LIBRARY_PATH"] = str(bundled.parent) + os.pathsep + str(bundle)
        return str(bundled), env
    if getattr(sys, "frozen", False):
        raise RuntimeError("Bundled xclip is missing. Reinstall this ThumbTalk build.")
    executable = shutil.which("xclip")
    if not executable:
        raise RuntimeError(
            "Install xclip for Linux source development, or use the complete ThumbTalk download."
        )
    # A system tool must not load PyInstaller's private libraries.
    if "LD_LIBRARY_PATH_ORIG" in env:
        env["LD_LIBRARY_PATH"] = env["LD_LIBRARY_PATH_ORIG"]
    else:
        env.pop("LD_LIBRARY_PATH", None)
    return executable, env


class XclipClipboard:
    def __init__(
        self, *, run=subprocess.run, sleep=time.sleep, log=lambda message: None
    ):
        self.executable, self.env = xclip_command()
        self.run, self.sleep, self.log = run, sleep, log
        self.closed = False
        self.token = self.payload = self.previous = None
        self.log("Linux clipboard: xclip; 300 ms paste grace; game receipt unconfirmed")

    def _call(self, *options, data=None, timeout=CLIPBOARD_TIMEOUT_SECONDS):
        try:
            result = self.run(
                [self.executable, "-selection", "clipboard", *options],
                input=data,
                # xclip forks an owner when publishing. A captured stdout pipe
                # stays open in that child and makes communicate() time out
                # despite successful publication. Only reads need an output pipe.
                stdout=subprocess.DEVNULL if "-in" in options else subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=self.env,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RuntimeError(
                "Clipboard helper failed or timed out. No automatic retry; check WoW chat."
            ) from error
        if result.returncode:
            raise RuntimeError(
                "Clipboard helper failed. No automatic retry; check WoW chat."
            )
        return result.stdout or b""

    def _publish(self, payload):
        self._call("-in", data=payload)
        # xclip's parent can exit before its forked owner's X request has been
        # flushed. Observe publication before returning to another copy/paste.
        # These are bounded read-only checks, never another write or send.
        deadline = time.monotonic() + CLIPBOARD_TIMEOUT_SECONDS
        while self._call("-out") != payload:
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "Clipboard publication was not ready. No automatic retry; check WoW chat."
                )
            self.sleep(0.01)

    def _check_token(self, token):
        if self.closed or not token or token != self.token:
            raise RuntimeError("Clipboard lease changed or was closed.")

    def acquire(self, text):
        return self._acquire(text, 255)

    def acquire_review(self, text):
        """Publish one complete local-review packet, without a game reply."""
        import re

        if not isinstance(text, str) or not re.fullmatch(
            r"TTREVIEW2:[0-9a-f]{8}:[0-9a-f]{2,510}:[0-9a-f]{8};", text
        ):
            raise ValueError("Invalid review clipboard packet.")
        return self._acquire(text, 600)

    def _acquire(self, text, maximum):
        if self.closed:
            raise RuntimeError("Clipboard provider is closed.")
        if (
            not isinstance(text, str)
            or not text
            or len(text.encode("utf-8")) > maximum
            or any(ord(c) < 32 or ord(c) == 127 for c in text)
        ):
            raise ValueError("Invalid clipboard draft.")
        if self.token is not None:
            raise RuntimeError("A clipboard transfer is already active.")
        previous = None
        try:
            targets = set(self._call("-out", "-target", "TARGETS").splitlines())
            if targets & TEXT_TARGETS:
                previous = self._call("-out")
        except RuntimeError:
            self.log(
                "Previous clipboard text unavailable; only the new plain-text draft will be available."
            )
        payload = text.encode("utf-8")
        self._publish(payload)
        self.token, self.payload, self.previous = uuid.uuid4().hex, payload, previous
        return self.token

    def arm(self, token):
        """Reject replaced clipboard text before paste or submit; never read game state."""
        self._check_token(token)
        if self._call("-out") != self.payload:
            raise RuntimeError(
                "Clipboard lease changed. Transfer stopped; check WoW chat."
            )

    def wait_for_paste(self, token):
        """Keep xclip serving during asynchronous reads; does not confirm a read."""
        self._check_token(token)
        self.sleep(PASTE_GRACE_SECONDS)

    def restore(self, token):
        if self.closed or self.token is None:
            return
        self._check_token(token)
        try:
            if self.previous is not None and self._call("-out") == self.payload:
                self._publish(self.previous)
        except RuntimeError:
            # Submission may already have happened. A cleanup failure must not
            # encourage another send or mask the original insertion failure.
            self.log(
                "Clipboard restoration unavailable; leaving it unchanged. No message retried."
            )
        finally:
            self.token = self.payload = self.previous = None

    def close(self):
        if not self.closed:
            if self.token is not None:
                self.restore(self.token)
            self.closed = True
        # Standard xclip owners remain until another copy replaces them. No Qt
        # worker or custom clipboard event loop is started by this provider.
