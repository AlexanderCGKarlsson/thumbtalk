"""Exercise the updater against a real CI-built installer and existing installation."""

import argparse
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "helper"))
from thumbtalk_update_install import (  # noqa: E402
    DownloadedUpdate,
    installation_directory,
    install_update,
    launch_windows_installer,
)
from thumbtalk_updates import UpdatePackage  # noqa: E402
from thumbtalk_version import VERSION, TEST_BUILD  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("--prefix", type=Path, required=True)
args = parser.parse_args()
system = platform.system()
name = (
    "thumbtalk-windows-setup.exe"
    if system == "Windows"
    else f"ThumbTalk-{VERSION}-build{TEST_BUILD}-linux.zip"
)
exe = args.prefix / "app" / ("thumbtalk.exe" if system == "Windows" else "thumbtalk")
assert installation_directory(executable=exe, frozen=True) == args.prefix
marker = args.prefix / "updater-preserve-fixture.txt"
marker.write_text("keep existing preferences")
manifest = args.prefix / "app/BUILD.json"
manifest.write_text(json.dumps({"version": "0.0.0", "build": "old fixture"}))
processes = []


def quiet_installer(command, *, env):
    # CI suppresses wizard interaction; production opens the normal installer.
    process = launch_windows_installer(
        command + ["/VERYSILENT", "/SUPPRESSMSGBOXES"], env=env
    )
    processes.append(process)
    return process


with tempfile.TemporaryDirectory(prefix="thumbtalk-updater-check-") as directory:
    path = Path(directory) / name
    shutil.copy2(root / "dist/release" / name, path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    url = f"https://github.com/AlexanderCGKarlsson/thumbtalk/releases/download/v{VERSION}/{name}"
    package = UpdatePackage(name, url, url + ".sha256", path.stat().st_size, digest)
    downloaded = DownloadedUpdate(package, path, digest)
    with patch(
        "thumbtalk_update_install.launch_windows_installer", side_effect=quiet_installer
    ):
        result = install_update(downloaded, args.prefix)
    for process in processes:
        assert process.wait(timeout=180) == 0, "Windows update installer failed"
    assert result == ("handoff" if system == "Windows" else "installed")
    installed = json.loads(manifest.read_text())
    assert installed["version"] == VERSION, installed
    assert installed["build"] == TEST_BUILD, installed
    assert marker.read_text() == "keep existing preferences"
    subprocess.run([str(exe), "--version"], check=True, timeout=30)
    marker.unlink()
    print(
        "Existing installation updated through the in-app installer handoff; version/build and preserved user files verified."
    )
