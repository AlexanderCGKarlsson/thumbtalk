"""Temporary plain-text clipboard lease on the game's display, never the overlay's.

A small Qt worker services clipboard requests while the delivery thread pastes.
It preserves MIME formats and does not overwrite anything copied during a lease.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import uuid

MARKER = "application/x-thumbtalk-clipboard-lease"
WORKER_STALL_SECONDS = 8.0
RESPONSE_TIMEOUT_SECONDS = 10.0


def watch_clipboard_loop(
    heartbeat,
    stopped,
    *,
    timeout=WORKER_STALL_SECONDS,
    interval=0.5,
    clock=time.monotonic,
    terminate=os._exit,
):
    """Release the OS clipboard if Qt stops dispatching events.

    Clipboard reads can enter a foreign application's synchronous selection
    conversion. Closing stdin alone cannot wake that call. Run independently
    of Qt; exiting this worker releases its display connection/ownership. The
    companion treats EOF as a failed transfer and never resends it.
    """
    while not stopped.wait(interval):
        if clock() - heartbeat[0] >= timeout:
            terminate(70)
            return


class ClipboardLease:
    def __init__(self):
        command = (
            [sys.executable, "--clipboard-worker"]
            if getattr(sys, "frozen", False)
            else [
                sys.executable,
                str(Path(__file__).with_name("thumbtalk_cli.py")),
                "--clipboard-worker",
            ]
        )
        env = dict(os.environ, QT_MAC_DISABLE_FOREGROUND_APPLICATION_TRANSFORM="1")
        env["QT_QPA_PLATFORM"] = {"win32": "windows", "darwin": "cocoa"}.get(
            sys.platform, "xcb"
        )
        # DISPLAY remains the game's X server. Never inherit Qt's -display
        # argument used to put the Gamescope overlay on its separate root server.
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        self.responses = queue.Queue()
        self.lock = threading.Lock()
        self.closed = False

        def read():
            try:
                for line in self.process.stdout:
                    try:
                        self.responses.put(json.loads(line))
                    except (ValueError, UnicodeDecodeError):
                        continue
            finally:
                self.responses.put(
                    {
                        "error": "Clipboard worker exited during transfer. "
                        "No automatic retry; check WoW chat before recording again."
                    }
                )

        threading.Thread(target=read, daemon=True).start()

    def request(self, action, **values):
        with self.lock:
            if self.closed:
                raise RuntimeError(
                    "Clipboard connection is closed. Restart ThumbTalk to retry."
                )
            token = uuid.uuid4().hex
            try:
                self.process.stdin.write(
                    json.dumps(dict(action=action, id=token, **values)) + "\n"
                )
                self.process.stdin.flush()
                result = self.responses.get(timeout=RESPONSE_TIMEOUT_SECONDS)
            except (OSError, ValueError, queue.Empty) as error:
                self.close(force=True)
                raise RuntimeError(
                    f"Clipboard worker stopped responding during {action}. Transfer stopped; no automatic retry. "
                    "Check WoW chat before recording again."
                ) from error
            if result.get("id") != token:
                self.close(force=True)
                raise RuntimeError(
                    result.get("error", "The clipboard response was lost.")
                    + f" (clipboard {action})"
                )
            if not result.get("ok"):
                # A rejected lease is not a broken pipe. The worker restores
                # it and remains available for the next user-requested action.
                raise RuntimeError(
                    result.get("error", "Clipboard paste was not ready.")
                )
            return token

    def acquire(self, text):
        if (
            not text
            or len(text.encode("utf-8")) > 255
            or any(ord(c) < 32 for c in text)
        ):
            raise ValueError("Invalid clipboard draft.")
        return self.request("acquire", text=text)

    def arm(self, token):
        self.request("arm", lease=token)

    def wait_for_read(self, token):
        self.request("read", lease=token)

    def restore(self, token):
        if not self.closed:
            self.request("restore", lease=token)

    def close(self, *, force=False):
        if not self.closed:
            self.closed = True
            # The worker restores first. On X11 it keeps serving the restored
            # clipboard until another owner takes over (no clipboard manager
            # is guaranteed in Gaming Mode). It never keeps an active draft.
            try:
                self.process.stdin.close()
            except OSError:
                pass
        if force:
            # A wedged owner cannot restore data. Release only our worker;
            # never terminate the game, desktop clipboard manager, or Steam.
            # Reap it as well: EOF is not proof that the process exited.
            try:
                if self.process.poll() is None:
                    self.process.terminate()
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                try:
                    self.process.kill()
                    self.process.wait(timeout=1)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            except OSError:
                pass


def worker_main():
    from PySide6.QtCore import QMimeData, QTimer
    from PySide6.QtWidgets import QApplication

    # Start before Qt initialization: a wedged X server/init is also bounded.
    heartbeat = [time.monotonic()]
    stopped = threading.Event()
    threading.Thread(
        target=watch_clipboard_loop, args=(heartbeat, stopped), daemon=True
    ).start()
    app = QApplication(["thumbtalk-clipboard"])
    app.setQuitOnLastWindowClosed(False)
    clipboard = app.clipboard()
    incoming = queue.Queue()
    saved, lease, value = None, None, None
    detached = False
    content, pending = None, None

    class TransferData(QMimeData):
        requested = False
        tracking = False

        def retrieveData(self, mime_type, preferred_type):
            data = super().retrieveData(mime_type, preferred_type)
            if self.tracking and mime_type.startswith("text/plain"):
                self.requested = True
            return data

    def read():
        try:
            for line in sys.stdin:
                if len(line) > 8192:
                    incoming.put({"id": "", "action": "invalid"})
                    continue
                try:
                    incoming.put(json.loads(line))
                except ValueError:
                    incoming.put({"id": "", "action": "invalid"})
        finally:
            incoming.put({"action": "close"})

    def restore():
        nonlocal saved, lease, value
        current = clipboard.mimeData()
        if (
            saved is not None
            and lease
            and clipboard.ownsClipboard()
            and current is not None
            and bytes(current.data(MARKER)).decode("ascii", errors="replace") == lease
            and current.text() == value
        ):
            clipboard.setMimeData(saved)
        saved, lease, value = None, None, None

    def current_text_matches():
        # Steam/Gamescope or a clipboard manager can take ownership after
        # copying the offered data. Accept its unchanged text, not ownership
        # alone. Never restore over data owned/copied by another application.
        current = clipboard.mimeData()
        return current is not None and current.text() == value

    def tick():
        nonlocal saved, lease, value, detached, content, pending
        heartbeat[0] = time.monotonic()
        if detached:
            if sys.platform != "linux" or not clipboard.ownsClipboard():
                app.quit()
            return
        if pending is not None:
            identity, deadline = pending
            managed = not clipboard.ownsClipboard()
            matches = current_text_matches() if managed else True
            if not matches or time.monotonic() > deadline:
                pending = None
                restore()
                print(
                    json.dumps(
                        {
                            "id": identity,
                            "error": "Paste was not read. Clear any partial chat before retrying.",
                        }
                    ),
                    flush=True,
                )
            elif managed or (content is not None and content.requested):
                pending = None
                print(json.dumps({"id": identity, "ok": True}), flush=True)
        while not incoming.empty():
            request = incoming.get()
            action, identity = request.get("action"), request.get("id")
            if action == "close":
                restore()
                detached = True
                timer.setInterval(500)
                return
            try:
                if action == "acquire":
                    text = request.get("text")
                    if (
                        not isinstance(text, str)
                        or not text
                        or len(text.encode("utf-8")) > 255
                        or any(ord(c) < 32 for c in text)
                    ):
                        raise ValueError("Invalid clipboard draft.")
                    restore()
                    snapshot = QMimeData()
                    previous = clipboard.mimeData()
                    size = 0
                    for format_name in (
                        previous.formats() if previous is not None else ()
                    ):
                        data = previous.data(format_name)
                        size += len(data)
                        if size > 64 * 1024 * 1024:
                            raise ValueError(
                                "Clipboard is too large to preserve; draft was not pasted."
                            )
                        snapshot.setData(format_name, data)
                    saved, lease, value = snapshot, identity, text
                    content = TransferData()
                    content.setText(text)
                    content.setData(MARKER, identity.encode("ascii"))
                    clipboard.setMimeData(content)
                    if clipboard.text() != text:
                        raise RuntimeError("Could not prepare the clipboard.")
                    # Ignore our own validation read, but retain external reads
                    # that happen before arm (e.g. eager clipboard caching).
                    content.tracking = True
                elif action in ("arm", "read"):
                    if request.get("lease") != lease or not lease:
                        raise ValueError(
                            "Clipboard lease changed before paste completed."
                        )
                    if not clipboard.ownsClipboard() and not current_text_matches():
                        raise ValueError(
                            "Clipboard lease changed before paste completed."
                        )
                    if action == "read":
                        pending = (identity, time.monotonic() + 5)
                        continue
                elif action == "restore":
                    if lease is not None and request.get("lease") != lease:
                        raise ValueError("Clipboard lease changed.")
                    restore()
                else:
                    raise ValueError("Unknown clipboard request.")
                print(json.dumps({"id": identity, "ok": True}), flush=True)
            except Exception as error:
                restore()
                print(json.dumps({"id": identity, "error": str(error)}), flush=True)

    threading.Thread(target=read, daemon=True).start()
    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(10)
    try:
        app.exec()
    finally:
        stopped.set()
