"""Build a self-contained release on the target operating system."""

from pathlib import Path
import hashlib
import json
from importlib.metadata import distribution
import os
import platform
import shutil
import subprocess
import sys
import tomllib

from packaging.version import Version

from collect_notices import collect_notices

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "helper"))
from thumbtalk_version import VERSION, TEST_BUILD  # noqa: E402 - local app source

project_version = tomllib.loads((root / "helper/pyproject.toml").read_text())[
    "project"
]["version"]
if project_version != VERSION:
    raise SystemExit(
        "App and package versions differ; update thumbtalk_version.py and pyproject.toml together."
    )
release_tag = os.environ.get("THUMBTALK_RELEASE_TAG")
if release_tag and Version(release_tag) != Version(VERSION):
    raise SystemExit("Release tag does not match the app version: " + release_tag)
system = platform.system()
if system not in ("Linux", "Windows"):
    raise SystemExit("Build on Linux or Windows; cross-compilation is not supported.")
if platform.machine().lower() not in ("x86_64", "amd64"):
    raise SystemExit("Release bundles target x86_64.")
work = root / "build"
work.mkdir(exist_ok=True)
args = [
    sys.executable,
    "-m",
    "PyInstaller",
    "--noconfirm",
    "--clean",
    "--onedir",
    "--name",
    "thumbtalk",
    "--distpath",
    str(root / "dist"),
    "--workpath",
    str(work / "pyinstaller"),
    "--specpath",
    str(work),
    "--paths",
    str(root / "helper"),
    "--add-data",
    str(root / "addon/ThumbTalk") + ":addon/ThumbTalk",
    "--add-data",
    str(root / "packaging/assets") + ":assets",
]
for package in (
    "faster_whisper",
    "ctranslate2",
    "tokenizers",
    "av",
    "pygame",
    "sherpa_onnx",
):
    args += ["--collect-all", package]
# Preserve wheel-provided native dependencies and their license metadata.
for name in ("sherpa-onnx", "sherpa-onnx-core"):
    args += ["--copy-metadata", name]
    package = distribution(name)
    for entry in package.files or ():
        if entry.parts[0].endswith(".libs"):
            args += [
                "--add-binary",
                str(package.locate_file(entry)) + ":" + str(entry.parent),
            ]
# pygame's tests and build integrations are not runtime dependencies.
for module in (
    "pygame.tests",
    "pygame.examples",
    "pygame.docs",
    "pygame.__pyinstaller",
    "pygame.__briefcase",
):
    args += ["--exclude-module", module]
for module in ("pynput.keyboard._dummy", "pynput.mouse._dummy"):
    args += ["--hidden-import", module]
backend = "win32" if system == "Windows" else "xorg"
for module in ("pynput.keyboard._" + backend, "pynput.mouse._" + backend):
    args += ["--hidden-import", module]
if system == "Linux":
    library = Path("/usr/lib/x86_64-linux-gnu/libportaudio.so.2")
    if not library.is_file():
        raise SystemExit("Install libportaudio2 on the build machine.")
    args += ["--add-binary", str(library) + ":."]
    args += ["--add-data", str(root / "packaging/fonts.conf") + ":fontconfig"]
    subprocess.run(["bash", str(root / "packaging/build-linux-input.sh")], check=True)
    for name in ("ydotool", "ydotoold"):
        args += ["--add-binary", str(work / "linux-input" / name) + ":input"]
    args += [
        "--add-data",
        str(root / "packaging/third-party/ydotool") + ":input/source",
    ]
    args += [
        "--add-data",
        str(root / "packaging/build-linux-input.sh") + ":input/source",
    ]
    xclip = shutil.which("xclip")
    if not xclip:
        raise SystemExit("Install xclip on the Linux build machine.")
    args += ["--add-binary", xclip + ":clipboard"]
    version = subprocess.check_output(
        ["dpkg-query", "-W", "-f=${Version}", "xclip"], text=True
    ).strip()
    if version != "0.13-2":
        raise SystemExit(
            "Update the bundled corresponding xclip source for version " + version
        )
    args += [
        "--add-data",
        str(root / "packaging/third-party/xclip") + ":clipboard/source",
    ]
    # Include the distribution's license notices alongside the helper.
    for dependency in (
        "xclip",
        "libx11-6",
        "libxmu6",
        "libxt6",
        "libxcb1",
        "libxext6",
        "libsm6",
        "libice6",
        "libxau6",
        "libxdmcp6",
        "libbsd0",
        "libmd0",
        "libuuid1",
    ):
        notice = Path("/usr/share/doc") / dependency / "copyright"
        if not notice.is_file():
            raise SystemExit("Missing Linux dependency copyright: " + dependency)
        args += ["--add-data", str(notice) + ":clipboard/licenses/" + dependency]
else:
    args += [
        "--hide-console",
        "hide-early",
        "--icon",
        str(root / "packaging/assets/thumbtalk.ico"),
    ]
    # Speech extensions can load before Qt adds its DLL directory. Include the
    # wheel's Microsoft C++ runtime at the bundle root, including Wine builds
    # where dependency discovery otherwise finds (and excludes) Wine substitutes.
    qt_package = distribution("PySide6-Essentials")
    for name in (
        "msvcp140.dll",
        "msvcp140_1.dll",
        "msvcp140_2.dll",
        "msvcp140_codecvt_ids.dll",
    ):
        library = Path(qt_package.locate_file("PySide6/" + name))
        if not library.is_file():
            raise SystemExit("Missing Windows runtime in Qt wheel: " + name)
        args += ["--add-binary", str(library) + ":."]
args.append(str(root / "helper/thumbtalk_cli.py"))
subprocess.run(args, check=True, cwd=root)
bundle = root / "dist/thumbtalk"
executable = bundle / ("thumbtalk.exe" if system == "Windows" else "thumbtalk")
for option in ("--help", "--list-languages", "--self-test"):
    result = subprocess.run(
        [str(executable), option],
        # Windows needs its native font database. Its offscreen backend does
        # not enumerate system fonts like the Linux offscreen backend does.
        env={
            **os.environ,
            "QT_QPA_PLATFORM": "windows" if system == "Windows" else "offscreen",
        },
        capture_output=True,
        text=True,
        errors="replace",
    )
    print(result.stdout, end="")
    print(result.stderr, end="", file=sys.stderr)
    result.check_returncode()
    if system == "Linux" and any(
        message in result.stderr
        for message in ("Fontconfig error:", "Fontconfig warning:")
    ):
        raise SystemExit("Fontconfig diagnostics in Linux bundle smoke check.")
shutil.copy2(root / "packaging/launch-with-thumbtalk.sh", bundle)
# Keep notices beside the executable in portable and installed bundles alike.
collect_notices(bundle / "THIRD_PARTY")
for document in (
    "README.md",
    "PRIVACY.md",
    "THIRD_PARTY_NOTICES.md",
):
    shutil.copy2(root / document, bundle)
# Preserve the artwork used by the bundled README.
(bundle / "docs/assets").mkdir(parents=True, exist_ok=True)
for artwork in (
    "thumbtalk-banner.png",
    "language-wheel.png",
    "button-setup.png",
    "wow-setup.png",
    "buy-me-a-coffee.svg",
):
    shutil.copy2(root / "docs/assets" / artwork, bundle / "docs/assets" / artwork)
(bundle / "BUILD.json").write_text(
    json.dumps(
        {
            "version": VERSION,
            "build": TEST_BUILD,
            "platform": system,
            "source_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=root, text=True
            ).strip(),
        },
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
release = root / "dist/release"
release.mkdir(exist_ok=True)
staging = work / "archive"
staging.mkdir(exist_ok=True)
if (staging / "ThumbTalk").exists():
    shutil.rmtree(staging / "ThumbTalk")
shutil.copytree(
    bundle,
    staging / "ThumbTalk",
    symlinks=system == "Linux",
    copy_function=os.link if system == "Linux" else shutil.copy2,
)
name = "thumbtalk-" + system.lower() + "-x86_64"
archive = Path(
    shutil.make_archive(
        str(release / name),
        "zip" if system == "Windows" else "gztar",
        root_dir=staging,
        base_dir="ThumbTalk",
    )
)
digest = hashlib.sha256(archive.read_bytes()).hexdigest()
archive.with_name(archive.name + ".sha256").write_text(
    digest + "  " + archive.name + "\n"
)
if system == "Windows":
    compiler = (
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
        / "Inno Setup 6/ISCC.exe"
    )
    version = tomllib.loads((root / "helper/pyproject.toml").read_text())["project"][
        "version"
    ]
    subprocess.run(
        [str(compiler), "/DAppVersion=" + version, str(root / "packaging/windows.iss")],
        check=True,
    )
    installer = release / "thumbtalk-windows-setup.exe"
    installer.with_suffix(".exe.sha256").write_text(
        hashlib.sha256(installer.read_bytes()).hexdigest()
        + "  "
        + installer.name
        + "\n"
    )
for name in (
    "install.sh",
    "install.ps1",
    "Install-ThumbTalk.cmd",
    "Install-ThumbTalk.desktop",
):
    shutil.copy2(root / name, release / name)
if system == "Linux":
    # One extraction and one click, including before any release is published.
    offline_name = f"ThumbTalk-{VERSION}-build{TEST_BUILD}-linux"
    offline = work / offline_name
    if offline.exists():
        shutil.rmtree(offline)
    offline.mkdir()
    for item in (
        archive,
        archive.with_name(archive.name + ".sha256"),
        root / "install.sh",
    ):
        shutil.copy2(item, offline / item.name)
    shutil.copy2(root / "packaging/install-local.sh", offline)
    shutil.copy2(
        root / "packaging/Install-ThumbTalk-Local.desktop",
        offline / "Install-ThumbTalk.desktop",
    )
    (offline / "VERSION.txt").write_text(f"ThumbTalk {VERSION} · build {TEST_BUILD}\n")
    offline_zip = Path(
        shutil.make_archive(
            str(release / offline_name), "zip", root_dir=work, base_dir=offline_name
        )
    )
    offline_zip.with_name(offline_zip.name + ".sha256").write_text(
        hashlib.sha256(offline_zip.read_bytes()).hexdigest()
        + "  "
        + offline_zip.name
        + "\n"
    )
print("Release artifacts:", release)
