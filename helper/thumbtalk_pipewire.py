"""Use the host PipeWire tools, independent of frozen ALSA libraries and plugins."""

import os
import shutil
import subprocess
import sys
import threading
from functools import lru_cache

SYSTEM_AUDIO = "System audio · PipeWire"


def available():
    return sys.platform == "linux" and (
        raw_option() is not None or shutil.which("pacat") is not None
    )


def host_environment():
    env = dict(os.environ)
    env["LC_ALL"] = "C"
    # PyInstaller's libraries belong to ThumbTalk, not to system audio tools.
    original = env.pop("LD_LIBRARY_PATH_ORIG", None)
    env.pop("LD_LIBRARY_PATH", None)
    if original:
        env["LD_LIBRARY_PATH"] = original
    return env


@lru_cache(maxsize=1)
def raw_option():
    executable = shutil.which("pw-cat")
    if not executable:
        return None
    try:
        result = subprocess.run(
            [executable, "--help"],
            capture_output=True,
            env=host_environment(),
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if b"--raw" in result.stdout:
        return ["--raw"]
    # Pipe streaming predates --raw, but is absent from early pw-cat versions.
    if b"[<file>|-]" in result.stdout or b"[FILE | -]" in result.stdout:
        return []
    return None


def command(direction):
    options = raw_option()
    if options is not None:
        return [
            shutil.which("pw-cat"),
            "--record" if direction == "input" else "--playback",
            *options,
            "--format=f32",
            "--rate=48000",
            "--channels=1",
            "--latency=100ms",
            "--target=auto",
            "-",
        ]
    compatible = shutil.which("pacat")
    if compatible:
        return [
            compatible,
            "--record" if direction == "input" else "--playback",
            "--raw",
            "--format=float32le",
            "--rate=48000",
            "--channels=1",
            "--latency-msec=100",
            "--client-name=ThumbTalk",
        ]
    raise ValueError("System audio tools are unavailable. Choose another microphone.")


class CaptureStream:
    def __init__(self, callback):
        self.callback = callback
        self.process = None
        self.closing = False
        self.error = ""
        self.reader = self.errors = None

    def start(self):
        self.process = subprocess.Popen(
            command("input"),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=host_environment(),
            bufsize=0,
        )
        self.reader = threading.Thread(target=self.read, daemon=True)
        self.errors = threading.Thread(target=self.read_errors, daemon=True)
        self.errors.start()
        self.reader.start()

    def read_errors(self):
        while data := self.process.stderr.read(256):
            self.error = (self.error + data.decode(errors="replace"))[-2048:]

    def read(self):
        import numpy as np

        pending = b""
        while data := self.process.stdout.read(3840):
            pending += data
            length = len(pending) - len(pending) % 4
            if length:
                samples = np.frombuffer(pending[:length], dtype="<f4").reshape(-1, 1)
                self.callback(samples, len(samples), None, None)
                pending = pending[length:]
        if not self.closing:
            self.error = (
                self.error
                or "The system microphone stopped. Check SteamOS Sound settings."
            )

    def stop(self):
        self.closing = True
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        for thread in (self.reader, self.errors):
            if thread:
                thread.join(timeout=1)

    def close(self):
        self.stop()
        if self.process:
            self.process.stdout.close()
            self.process.stderr.close()


class Playback:
    def __init__(self, samples):
        self.error = ""
        self.process = subprocess.Popen(
            command("output"),
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env=host_environment(),
        )

        def feed():
            try:
                _, errors = self.process.communicate(samples.astype("<f4").tobytes())
                if self.process.returncode:
                    self.error = errors.decode(errors="replace")[-2048:]
            except OSError as error:
                self.error = str(error)

        self.thread = threading.Thread(target=feed, daemon=True)
        self.thread.start()

    def stop(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.thread.join(timeout=2)
