"""Arm a one-run sampler around the existing launcher, without changing game input."""

import argparse
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile

MARKER = "# ThumbTalk one-run process diagnostics v1"
WRAPPER = """#!/usr/bin/env bash
# ThumbTalk one-run process diagnostics v1
set -u
root="$(cd -- "$(dirname -- "$0")" && pwd)"
backup="$root/launch-with-thumbtalk.sh.before-diagnostics"
if [ ! -f "$backup" ]; then
    echo "Diagnostic launcher backup missing. Restore the normal ThumbTalk launcher before playing." >&2
    exit 2
fi
# Restore the exact original file before launching. Normal later launches are unchanged.
mv -- "$backup" "$root/launch-with-thumbtalk.sh" || exit 2
state="${XDG_STATE_HOME:-$HOME/.local/state}/thumbtalk"
mkdir -p "$state"
env -u LD_LIBRARY_PATH -u LD_PRELOAD -u PYTHONHOME -u PYTHONPATH \\
    /usr/bin/python3 "$root/hang-diagnostics/collect.py" --detach --parent "$$" \\
    --output "$state/hang-report.jsonl" || echo "Diagnostic sampler could not start." >&2
exec "$root/launch-with-thumbtalk.sh" "$@"
"""


def arm(prefix, source):
    launcher = prefix / "launch-with-thumbtalk.sh"
    backup = prefix / "launch-with-thumbtalk.sh.before-diagnostics"
    if launcher.is_symlink() or not launcher.is_file():
        raise ValueError(
            "The installed ThumbTalk Steam launcher was not found as a regular file"
        )
    original = launcher.read_bytes()
    if MARKER.encode() in original or backup.exists():
        raise ValueError(
            "A diagnostic launch is already armed; use Remove diagnostics to undo it"
        )
    mode = stat.S_IMODE(launcher.stat().st_mode)
    destination = prefix / "hang-diagnostics"
    destination.mkdir(exist_ok=True)
    shutil.copyfile(source / "collect.py", destination / "collect.py")
    # Keep an exclusive backup; never overwrite an earlier pending restore.
    with backup.open("xb") as stream:
        stream.write(original)
    backup.chmod(mode)
    fd, temporary = tempfile.mkstemp(prefix=".diagnostic-launch-", dir=prefix)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(WRAPPER)
        os.chmod(temporary, mode | stat.S_IXUSR)
        os.replace(temporary, launcher)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        backup.unlink(missing_ok=True)
        raise


def disarm(prefix):
    launcher = prefix / "launch-with-thumbtalk.sh"
    backup = prefix / "launch-with-thumbtalk.sh.before-diagnostics"
    if not backup.exists():
        return False
    if launcher.is_symlink() or MARKER.encode() not in launcher.read_bytes():
        raise ValueError(
            "The launcher changed after arming; the backup was left intact"
        )
    os.replace(backup, launcher)
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--remove", action="store_true")
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("This diagnostic is for the SteamOS/Linux device")
    prefix = (
        Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
        / "thumbtalk"
    )
    try:
        if args.remove:
            restored = disarm(prefix)
            print(
                "Normal launcher restored."
                if restored
                else "No pending diagnostic launch. Normal launcher unchanged."
            )
        else:
            arm(prefix, Path(__file__).resolve().parent)
            print(
                "Armed for the next Steam launch using ThumbTalk. No launch-option change is needed."
            )
            print(
                "The original launcher restores itself immediately. Sampling lasts at most 15 minutes."
            )
            print(
                "After closing the game, send ~/.local/state/thumbtalk/hang-report.jsonl."
            )
    except (OSError, ValueError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
