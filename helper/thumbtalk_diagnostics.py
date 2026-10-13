"""Bounded local delivery trace; never given audio or transcript content."""

from pathlib import Path
import threading
import time


class DeliveryLog:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()

    def __call__(self, message):
        line = time.strftime("%Y-%m-%d %H:%M:%S ") + str(message) + "\n"
        print(line, end="", flush=True)
        # Diagnostics must not prevent recording or cause a delivery retry.
        try:
            with self.lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if self.path.exists() and self.path.stat().st_size >= 256 * 1024:
                    self.path.replace(self.path.with_suffix(".previous.log"))
                with self.path.open("a", encoding="utf-8") as stream:
                    stream.write(line)
        except OSError:
            pass
