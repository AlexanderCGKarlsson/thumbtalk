"""Read-only, bounded Linux process sampler; no debugger, memory, or clipboard access."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

GAME_NAMES = {
    "wow.exe",
    "wowb.exe",
    "wowt.exe",
    "wowclassic.exe",
    "wowclassicb.exe",
    "wowclassict.exe",
}
OTHER_NAMES = {"thumbtalk", "gamescope", "xwayland", "wineserver", "wineserver64"}
MAX_BYTES = 16 * 1024 * 1024


def stat_fields(text):
    # comm can contain spaces and parentheses. Fields after its last ')' start at state.
    fields = text.rsplit(") ", 1)[1].split()
    return dict(
        state=fields[0],
        ppid=int(fields[1]),
        user_ticks=int(fields[11]),
        system_ticks=int(fields[12]),
        start_ticks=int(fields[19]),
    )


def process_kind(directory):
    name = directory.joinpath("comm").read_text().strip().lower()
    if name in GAME_NAMES:
        return name
    if name in OTHER_NAMES:
        return name
    # Wine can call its main thread MainThread. Inspect executable arguments only
    # to classify it; never include the command line or its arguments in a report.
    if name not in {
        "mainthread",
        "wine",
        "wine64",
        "wine-preloader",
        "wine64-preloade",
    }:
        return None
    arguments = directory.joinpath("cmdline").read_bytes()[:16384].split(b"\0")
    for argument in arguments[:4]:
        base = (
            argument.decode("utf-8", "replace")
            .replace("\\", "/")
            .rsplit("/", 1)[-1]
            .lower()
        )
        if base in GAME_NAMES:
            return base
    return None


def snapshot(proc=Path("/proc"), uid=None):
    uid = os.getuid() if uid is None else uid
    found = []
    for directory in proc.iterdir():
        if not directory.name.isdigit():
            continue
        try:
            if directory.stat().st_uid != uid:
                continue
            kind = process_kind(directory)
            if kind is None:
                continue
            row = dict(
                pid=int(directory.name),
                kind=kind,
                **stat_fields(directory.joinpath("stat").read_text()),
            )
            threads = []
            tasks = sorted(
                directory.joinpath("task").iterdir(), key=lambda p: int(p.name)
            )
            row["thread_count"] = len(tasks)
            for task in tasks[:256]:
                try:
                    item = dict(
                        tid=int(task.name),
                        **stat_fields(task.joinpath("stat").read_text()),
                    )
                    try:
                        item["wait_channel"] = (
                            task.joinpath("wchan").read_text().strip()[:128]
                        )
                    except OSError:
                        item["wait_channel"] = "unavailable"
                    threads.append(item)
                except (OSError, ValueError, IndexError):
                    continue
            row["threads"] = threads
            found.append(row)
            if len(found) >= 24:
                break
        except (OSError, ValueError, IndexError):
            continue
    return found


def parent_identity(pid):
    try:
        return stat_fields(Path(f"/proc/{pid}/stat").read_text())["start_ticks"]
    except (OSError, ValueError, IndexError):
        return None


def collect(parent, output, seconds):
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.is_symlink():
        raise ValueError("Diagnostic output must not be a symlink")
    if output.exists():
        output.replace(output.with_suffix(output.suffix + ".previous"))
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    origin, started, missing_since = parent_identity(parent), time.monotonic(), None
    with os.fdopen(fd, "w") as stream:

        def write(record):
            stream.write(json.dumps(record, separators=(",", ":")) + "\n")
            stream.flush()

        write(
            dict(
                format="thumbtalk-process-report-1",
                kernel=os.uname().release,
                started_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                clock_ticks=os.sysconf("SC_CLK_TCK"),
                interval_seconds=2,
                contents="Process/thread states and CPU counters only; no memory, environment, clipboard, or chat text.",
            )
        )
        reason = "time limit"
        while time.monotonic() - started < seconds:
            write(
                dict(elapsed=round(time.monotonic() - started, 2), processes=snapshot())
            )
            if stream.tell() >= MAX_BYTES:
                reason = "size limit"
                break
            if origin is None or parent_identity(parent) != origin:
                missing_since = missing_since or time.monotonic()
                if time.monotonic() - missing_since >= 6:
                    reason = "launcher exited"
                    break
            time.sleep(2)
        write(dict(finished=reason))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seconds", type=int, default=900)
    parser.add_argument("--detach", action="store_true")
    args = parser.parse_args()
    if sys.platform != "linux" or args.parent <= 1 or not 1 <= args.seconds <= 900:
        parser.error("Linux, a valid parent PID, and a 1–900 second limit are required")
    if args.detach:
        subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--parent",
                str(args.parent),
                "--output",
                str(args.output),
                "--seconds",
                str(args.seconds),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    else:
        collect(args.parent, args.output, args.seconds)


if __name__ == "__main__":
    main()
