"""Download verified release packages and hand them to the existing installers."""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import ssl
import stat
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPSHandler, HTTPRedirectHandler
import zipfile

import certifi

from packaging.version import Version

from thumbtalk_updates import REPOSITORY, UpdatePackage


class UpdateCancelled(Exception):
    pass


@dataclass(frozen=True)
class DownloadedUpdate:
    package: UpdatePackage
    path: Path
    sha256: str

    @property
    def directory(self):
        return self.path.parent


def installation_directory(*, executable=None, frozen=None, system=None):
    """Only update a marked installation, never a source tree or portable folder."""
    system = system or platform.system()
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    if not frozen or system not in ("Windows", "Linux"):
        return None
    exe = Path(executable or sys.executable).absolute()
    prefix = exe.parent.parent
    if (
        exe.parent.name != "app"
        or exe.name != ("thumbtalk.exe" if system == "Windows" else "thumbtalk")
        or not (prefix / ".thumbtalk-install").is_file()
        or prefix.is_symlink()
        or exe.parent.is_symlink()
    ):
        return None
    return prefix


def _allowed_url(url):
    value = urlsplit(url)
    return (
        value.scheme == "https"
        and value.hostname
        in (
            "github.com",
            "release-assets.githubusercontent.com",
            "objects.githubusercontent.com",
        )
        and value.port in (None, 443)
        and not value.username
        and not value.password
    )


class ReleaseRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _allowed_url(newurl):
            raise ValueError("The update download redirected outside GitHub.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_download(url):
    if not _allowed_url(url):
        raise ValueError("The update address is not a GitHub HTTPS download.")
    opener = build_opener(
        ReleaseRedirects(),
        HTTPSHandler(context=ssl.create_default_context(cafile=certifi.where())),
    )
    return opener.open(
        Request(url, headers={"User-Agent": "ThumbTalk-updater"}), timeout=20
    )


def _cancelled(cancel):
    if cancel.is_set():
        raise UpdateCancelled()


def download_update(package, cancel, progress, *, opener=None, temp_parent=None):
    """Stream with limits, verify before exposing an installable file, clean failures."""
    base = f"https://github.com/{REPOSITORY}/releases/download/"
    if (
        not package.url.startswith(base)
        or not _allowed_url(package.url)
        or package.url.rsplit("/", 1)[-1] != package.name
        or "/" in package.name
        or "\\" in package.name
        or package.checksum_url != package.url + ".sha256"
        or not 0 < package.size <= 1024 * 1024 * 1024
    ):
        raise ValueError("Invalid update package.")
    directory = Path(tempfile.mkdtemp(prefix="thumbtalk-update-", dir=temp_parent))
    opener = opener or _open_download
    try:
        _cancelled(cancel)
        with opener(package.checksum_url) as response:
            checksum = response.read(4097)
        _cancelled(cancel)
        if len(checksum) > 4096:
            raise ValueError("The update checksum is invalid.")
        parts = checksum.decode("ascii").split()
        if (
            len(parts) != 2
            or not re.fullmatch(r"[a-fA-F0-9]{64}", parts[0])
            or parts[1] != package.name
        ):
            raise ValueError("The update checksum does not match this package name.")
        expected = parts[0].lower()
        if package.digest and expected != package.digest:
            raise ValueError(
                "GitHub's package digest and checksum disagree. Check for updates again."
            )
        if shutil.disk_usage(directory).free < package.size * 10 + 256 * 1024 * 1024:
            raise OSError(
                "There is not enough free space to download and install this update."
            )
        partial = directory / (package.name + ".part")
        digest = hashlib.sha256()
        received = 0
        progress(0, package.size)
        with opener(package.url) as response, partial.open("xb") as output:
            while True:
                _cancelled(cancel)
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                received += len(chunk)
                if received > package.size:
                    raise ValueError("The update download is larger than expected.")
                output.write(chunk)
                digest.update(chunk)
                progress(received, package.size)
        _cancelled(cancel)
        if received != package.size or digest.hexdigest() != expected:
            raise ValueError(
                "The update download did not pass verification. Please download it again."
            )
        target = directory / package.name
        partial.replace(target)
        return DownloadedUpdate(package, target, expected)
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise


def verify_download(download):
    digest = hashlib.sha256()
    with download.path.open("rb") as source:
        for chunk in iter(lambda: source.read(256 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != download.sha256:
        raise ValueError("The downloaded update changed. Please download it again.")


def extract_linux_update(download):
    """The offline ZIP contains six regular files, including a nested app archive."""
    folder = download.path.stem
    allowed = {
        "thumbtalk-linux-x86_64.tar.gz",
        "thumbtalk-linux-x86_64.tar.gz.sha256",
        "install.sh",
        "install-local.sh",
        "Install-ThumbTalk.desktop",
        "VERSION.txt",
    }
    target = download.directory / "extracted"
    if target.exists():
        shutil.rmtree(target)
    with zipfile.ZipFile(download.path) as archive:
        entries = archive.infolist()
        if (
            len(entries) > 7
            or sum(item.file_size for item in entries)
            > download.package.size * 2 + 1024 * 1024
        ):
            raise ValueError("The Linux update expands beyond its expected size.")
        seen = set()
        for item in entries:
            name = PurePosixPath(item.filename)
            mode = item.external_attr >> 16
            if item.is_dir() and item.filename == folder + "/":
                continue
            if (
                len(name.parts) != 2
                or name.parts[0] != folder
                or name.parts[1] not in allowed
                or item.filename in seen
                or "\\" in item.filename
                or item.is_dir()
                or stat.S_ISLNK(mode)
                or (stat.S_IFMT(mode) and not stat.S_ISREG(mode))
                or item.file_size > 1024 * 1024 * 1024
            ):
                raise ValueError("The Linux update contains unexpected files.")
            seen.add(item.filename)
        if seen != {folder + "/" + name for name in allowed}:
            raise ValueError("The Linux update package is incomplete.")
        archive.extractall(target)
    return target / folder


def installer_environment():
    env = dict(os.environ)
    original = env.pop("LD_LIBRARY_PATH_ORIG", None)
    env.pop("LD_LIBRARY_PATH", None)
    if original:
        env["LD_LIBRARY_PATH"] = original
    bundle = str(getattr(sys, "_MEIPASS", ""))
    if bundle:
        env["PATH"] = os.pathsep.join(
            p for p in env.get("PATH", "").split(os.pathsep) if not p.startswith(bundle)
        )
    for key in (
        "QT_PLUGIN_PATH",
        "QT_QPA_PLATFORM_PLUGIN_PATH",
        "FONTCONFIG_FILE",
        "FONTCONFIG_PATH",
    ):
        env.pop(key, None)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def launch_windows_installer(command, *, env):
    # A frozen process changes the DLL search path inherited by installers.
    import ctypes

    kernel = ctypes.windll.kernel32
    buffer = ctypes.create_unicode_buffer(32768)
    kernel.GetDllDirectoryW(len(buffer), buffer)
    kernel.SetDllDirectoryW(None)
    try:
        return subprocess.Popen(
            command, cwd=str(Path(command[0]).parent), env=env, close_fds=True
        )
    finally:
        kernel.SetDllDirectoryW(buffer.value or None)


def install_update(download, prefix, *, system=None):
    """Windows hands off to visible Setup; Linux reports failure in the open dialog."""
    system = system or platform.system()
    prefix = Path(prefix)
    if not (prefix / ".thumbtalk-install").is_file() or prefix.is_symlink():
        raise ValueError("The ThumbTalk installation could not be identified.")
    verify_download(download)
    env = installer_environment()
    if system == "Windows" and download.package.name == "thumbtalk-windows-setup.exe":
        launch_windows_installer(
            [
                str(download.path),
                "/NORESTART",
                "/NOFORCECLOSEAPPLICATIONS",
                "/DIR=" + str(prefix),
                "/LOG=" + str(download.directory / "installer.log"),
            ],
            env=env,
        )
        return "handoff"
    if system == "Linux" and re.fullmatch(
        r"ThumbTalk-[0-9][A-Za-z0-9.+_-]*-build[0-9]+-linux\.zip", download.package.name
    ):
        folder = extract_linux_update(download)
        log = download.directory / "installer.log"
        with log.open("wb") as output:
            process = subprocess.run(
                [
                    "/bin/bash",
                    str(folder / "install-local.sh"),
                    "--prefix",
                    str(prefix),
                    "--no-launch",
                ],
                cwd=folder,
                env=env,
                stdout=output,
                stderr=subprocess.STDOUT,
            )
        if process.returncode:
            raise RuntimeError("The installer could not finish. See " + str(log))
        expected_version = Version(download.package.url.split("/")[-2])
        try:
            installed = json.loads((prefix / "app/BUILD.json").read_text())
            matches = Version(installed["version"]) == expected_version
        except (OSError, KeyError, ValueError):
            matches = False
        if not matches:
            raise RuntimeError(
                "The expected version was not installed. See " + str(log)
            )
        return "installed"
    raise ValueError("This update does not match your operating system.")


def restart_installed(prefix):
    executable = Path(prefix) / "app/thumbtalk"
    subprocess.Popen(
        [str(executable), "--setup"],
        env=installer_environment(),
        start_new_session=True,
        close_fds=True,
    )
