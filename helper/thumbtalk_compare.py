"""Isolated, sequential CPU comparisons using the same in-memory recording."""

from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

CANDIDATES = {
    "economy": "Whisper Tiny",
    "balanced": "Whisper Base",
    "accuracy": "Whisper Small",
    "parakeet": "Parakeet V3",
}
DEFAULT_CANDIDATES = ("balanced", "accuracy", "parakeet")
MAX_BYTES = 30 * 16000 * 4


def read_request(stream):
    header = json.loads(stream.readline(4096))
    size = header.get("size")
    if (
        header.get("preset") not in CANDIDATES
        or type(size) is not int
        or not 16000 <= size <= MAX_BYTES
        or size % 4
    ):
        raise ValueError("Choose a model and a recording between 0.25 and 30 seconds.")
    payload = stream.read(size)
    if len(payload) != size:
        raise ValueError("The recording was incomplete.")
    validate_threads(header.get("cpu_threads", 2))
    if type(header.get("compare_cpu", False)) is not bool:
        raise ValueError("Invalid comparison mode.")
    return header, payload


def validate_threads(value):
    if type(value) is not int or not 1 <= value <= 64:
        raise ValueError("Choose between 1 and 64 CPU threads for comparison.")


def peak_memory_mib():
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                (name, ctypes.c_size_t)
                for name in (
                    "PeakWorkingSetSize",
                    "WorkingSetSize",
                    "QuotaPeakPagedPoolUsage",
                    "QuotaPagedPoolUsage",
                    "QuotaPeakNonPagedPoolUsage",
                    "QuotaNonPagedPoolUsage",
                    "PagefileUsage",
                    "PeakPagefileUsage",
                )
            ]

        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        api = ctypes.WinDLL("psapi", use_last_error=True).GetProcessMemoryInfo
        api.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        if not api(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        return counters.PeakWorkingSetSize / 1048576
    import resource

    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value / (1048576 if sys.platform == "darwin" else 1024)


def lower_priority():
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.GetCurrentProcess.restype = wintypes.HANDLE
            kernel.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            return bool(kernel.SetPriorityClass(kernel.GetCurrentProcess(), 0x4000))
        os.nice(10)
        return True
    except OSError:
        return False


def worker_main():
    def emit(kind, **values):
        print(json.dumps(dict(kind=kind, **values), ensure_ascii=False), flush=True)

    try:
        header, payload = read_request(sys.stdin.buffer)
        threads = header.get("cpu_threads", 2)
        for name in (
            "OMP_NUM_THREADS",
            "OPENBLAS_NUM_THREADS",
            "MKL_NUM_THREADS",
            "VECLIB_MAXIMUM_THREADS",
        ):
            os.environ[name] = str(threads)
        # Match live dictation's normal process priority for the CPU comparison.
        priority = False if header.get("compare_cpu", False) else lower_priority()
        import numpy as np
        from thumbtalk_speech import PRESETS, SpeechSettings, create_transcriber

        audio = np.frombuffer(payload, dtype="<f4").copy()
        if not np.isfinite(audio).all():
            raise ValueError("The recording contains invalid audio samples.")
        preset = header["preset"]
        settings = SpeechSettings(
            preset=preset,
            model=PRESETS[preset]["model"],
            cpu_threads=threads,
            language=None if preset == "parakeet" else header.get("language"),
        )
        started = time.perf_counter()
        transcriber = create_transcriber(
            settings,
            lambda message: emit("progress", message=message),
            local_files_only=True,
        )
        load_seconds = time.perf_counter() - started
        times, cpu, texts = [], [], []
        for index in range(3):
            emit(
                "progress",
                message=f"Transcribing the same recording · pass {index + 1} of 3…",
            )
            started, cpu_started = time.perf_counter(), time.process_time()
            texts.append(transcriber.transcribe(audio))
            times.append(time.perf_counter() - started)
            cpu.append(time.process_time() - cpu_started)
        emit(
            "result",
            preset=preset,
            cpu_threads=threads,
            compare_cpu=header.get("compare_cpu", False),
            text=texts[-1],
            consistent=len(set(texts)) == 1,
            load_seconds=load_seconds,
            first_seconds=times[0],
            warm_seconds=sum(times[1:]) / 2,
            cpu_percent=100 * sum(cpu[1:]) / max(sum(times[1:]), 0.0001),
            peak_mib=peak_memory_mib(),
            audio_seconds=len(audio) / 16000,
            background_priority=priority,
        )
    except Exception as error:
        emit("error", message=str(error))
        raise SystemExit(1) from error


class Comparison:
    """No recording files, input listeners, or game delivery in the workers."""

    def __init__(
        self, audio, language, presets=None, *, cpu_threads=2, compare_cpu=False
    ):
        import numpy as np

        self.payload = np.asarray(audio, dtype="<f4").reshape(-1).tobytes()
        if not 16000 <= len(self.payload) <= MAX_BYTES:
            raise ValueError("Record a sentence between 0.25 and 30 seconds first.")
        self.language = language
        self.presets = (
            DEFAULT_CANDIDATES if presets is None else tuple(dict.fromkeys(presets))
        )
        if not self.presets or any(p not in CANDIDATES for p in self.presets):
            raise ValueError("Choose downloaded models to compare.")
        validate_threads(cpu_threads)
        if compare_cpu and len(self.presets) != 1:
            raise ValueError("Choose one model to compare CPU speeds.")
        self.compare_cpu = compare_cpu
        self.jobs = tuple(
            (preset, threads)
            for preset in self.presets
            for threads in ((2, 4) if compare_cpu else (cpu_threads,))
        )
        self.events = queue.Queue()
        self.cancelled = threading.Event()
        self.lock = threading.Lock()
        self.process = None
        self.thread = threading.Thread(target=self.run, daemon=True)

    def start(self):
        self.thread.start()

    def cancel(self):
        self.cancelled.set()
        with self.lock:
            if self.process and self.process.poll() is None:
                self.process.kill()

    def run(self):
        try:
            for preset, threads in self.jobs:
                label = f"{CANDIDATES[preset]} · {threads} threads"
                with self.lock:
                    if self.cancelled.is_set():
                        break
                    command = (
                        [sys.executable, "--compare-worker"]
                        if getattr(sys, "frozen", False)
                        else [
                            sys.executable,
                            str(Path(__file__).with_name("thumbtalk_cli.py")),
                            "--compare-worker",
                        ]
                    )
                    env = dict(
                        os.environ,
                        OMP_NUM_THREADS=str(threads),
                        OPENBLAS_NUM_THREADS=str(threads),
                        MKL_NUM_THREADS=str(threads),
                        VECLIB_MAXIMUM_THREADS=str(threads),
                    )
                    self.process = subprocess.Popen(
                        command,
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                        env=env,
                        creationflags=subprocess.CREATE_NO_WINDOW
                        if sys.platform == "win32"
                        else 0,
                    )
                    process = self.process
                self.events.put(
                    {"kind": "progress", "message": f"{label} · preparing…"}
                )
                reported = False
                try:
                    request = dict(
                        preset=preset,
                        language=self.language,
                        size=len(self.payload),
                        cpu_threads=threads,
                        compare_cpu=self.compare_cpu,
                    )
                    process.stdin.write(
                        json.dumps(request).encode() + b"\n" + self.payload
                    )
                    process.stdin.close()
                    for line in process.stdout:
                        try:
                            event = json.loads(line)
                        except (ValueError, UnicodeDecodeError):
                            continue
                        if event.get("kind") in ("result", "error"):
                            reported = True
                        if event.get("kind") in ("progress", "error"):
                            event["message"] = (
                                label + " · " + str(event.get("message", ""))
                            )
                        self.events.put(event)
                except (OSError, ValueError) as error:
                    if not self.cancelled.is_set():
                        self.events.put(
                            {"kind": "error", "message": label + " · " + str(error)}
                        )
                    reported = True
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.wait()  # Reclaim this model before starting another.
                    process.stdout.close()
                    process.stdin.close()
                    with self.lock:
                        self.process = None
                if not reported and not self.cancelled.is_set():
                    self.events.put(
                        {
                            "kind": "error",
                            "message": label
                            + " · The speech worker exited without a result. Check available memory.",
                        }
                    )
        except Exception as error:
            self.events.put(
                {
                    "kind": "error",
                    "message": "Could not run the comparison: " + str(error),
                }
            )
        finally:
            self.payload = b""
            self.events.put({"kind": "done", "cancelled": self.cancelled.is_set()})
