"""Installation helpers used by guided setup; no game or input access."""

from __future__ import annotations

import os
import platform
import re
import shlex
import time
from collections import deque
import shutil
import sys
import tempfile
from pathlib import Path


def resources():
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))


def steam_launch_option():
    # Per-user installation is stable even when a release is updated.
    path = Path.home() / ".local/share/thumbtalk/launch-with-thumbtalk.sh"
    if not path.is_file():
        raise RuntimeError(
            "Install the ThumbTalk Linux app first, then reopen its settings."
        )
    return shlex.quote(str(path)) + " %command%"


WOW_CLIENTS = {
    "_retail_": "WoW Retail",
    "_classic_": "WoW Classic",
    "_classic_era_": "WoW Classic Era / Hardcore",
    "_classic_beta_": "WoW Classic Beta",
    "_classic_ptr_": "WoW Classic PTR",
    "_beta_": "WoW Retail Beta",
    "_ptr_": "WoW Retail PTR",
    "_xptr_": "WoW Retail PTR (alternate)",
}


def wow_directories(folder: Path) -> list[Path]:
    """Resolve a client folder or all installed clients under a WoW root."""
    try:
        folder = folder.expanduser().resolve()
        candidates = (
            [folder]
            if folder.name.casefold() in WOW_CLIENTS
            else [folder / name for name in WOW_CLIENTS]
        )
        return [path.resolve() for path in candidates if installed_client(path)]
    except (OSError, RuntimeError):
        return []


def installed_client(folder: Path) -> bool:
    # Fresh Battle.net installs may not have created Interface yet.
    if (folder / "Interface").is_dir():
        return True
    try:
        executables = {
            "wow.exe",
            "wowclassic.exe",
            "wowclassicb.exe",
            "wowb.exe",
            "world of warcraft.app",
        }
        return any(
            entry.name.casefold() in executables
            and (
                entry.is_file()
                or (entry.name.casefold().endswith(".app") and entry.is_dir())
            )
            for entry in folder.iterdir()
        )
    except OSError:
        return False


def wow_directory(folder: Path) -> Path | None:
    """Never silently choose between multiple game versions."""
    candidates = wow_directories(folder)
    return candidates[0] if len(candidates) == 1 else None


def wow_label(folder: Path) -> str:
    return WOW_CLIENTS.get(folder.name.casefold(), "World of Warcraft")


def wow_search_roots(home=None, system=None, environ=None):
    """Known launcher locations; never search the entire home or system drive."""
    home = Path.home() if home is None else Path(home)
    system = system or platform.system()
    env = os.environ if environ is None else environ
    roots = [home / "Games", home / "World of Warcraft"]
    steam = []
    if system == "Windows":
        for name in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
            if env.get(name):
                base = Path(env[name])
                roots.append(base / "World of Warcraft")
                steam.append(base / "Steam")
        # Include custom installs recorded by Windows/Battle.net.
        if os.name == "nt":
            import ctypes
            import winreg

            for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
                drive = letter + ":\\"
                if ctypes.windll.kernel32.GetDriveTypeW(drive) in (2, 3):
                    base = Path(drive)
                    roots.extend(
                        base / name
                        for name in ("Games", "World of Warcraft", "Battle.net")
                    )
                    steam.extend(base / name for name in ("SteamLibrary", "Steam"))
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
                    try:
                        with winreg.OpenKey(
                            hive,
                            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
                            0,
                            winreg.KEY_READ | view,
                        ) as parent:
                            for index in range(winreg.QueryInfoKey(parent)[0]):
                                try:
                                    with winreg.OpenKey(
                                        parent, winreg.EnumKey(parent, index)
                                    ) as key:
                                        name = winreg.QueryValueEx(key, "DisplayName")[
                                            0
                                        ]
                                        if "world of warcraft" in str(name).casefold():
                                            location = winreg.QueryValueEx(
                                                key, "InstallLocation"
                                            )[0]
                                            if location:
                                                roots.append(Path(location))
                                except OSError:
                                    continue
                    except OSError:
                        continue
    elif system == "Darwin":
        roots.extend(
            [
                Path("/Applications/World of Warcraft"),
                home / "Applications/World of Warcraft",
            ]
        )
        steam.append(home / "Library/Application Support/Steam")
    else:
        data = Path(env.get("XDG_DATA_HOME", str(home / ".local/share")))
        roots.extend(
            [
                home / ".wine/drive_c",
                data / "lutris",
                data / "bottles/bottles",
                home / ".var/app/com.usebottles.bottles/data/bottles/bottles",
                home / ".var/app/net.lutris.Lutris/data/lutris",
            ]
        )
        steam.extend(
            [
                data / "Steam",
                home / ".steam/steam",
                home / ".steam/root",
                home / ".var/app/com.valvesoftware.Steam/data/Steam",
            ]
        )
        # Removable drives commonly hold a Steam library or a Games folder.
        for mount_parent in (
            Path("/run/media") / home.name,
            Path("/media") / home.name,
            Path("/mnt"),
        ):
            try:
                mounts = list(mount_parent.iterdir())
            except OSError:
                continue
            for mount in mounts:
                roots.extend(mount / name for name in ("Games", "World of Warcraft"))
                steam.extend([mount, mount / "SteamLibrary", mount / "Steam"])
    # Steam records libraries on other drives in libraryfolders.vdf.
    for base in list(steam):
        try:
            text = (base / "steamapps/libraryfolders.vdf").read_text(errors="replace")
            for value in re.findall(r'"path"\s*"((?:\\.|[^"\\])*)"', text):
                steam.append(Path(value.replace(r"\\", "\\").replace(r"\"", '"')))
        except OSError:
            continue
    for base in steam:
        roots.extend([base / "steamapps/common", base / "steamapps/compatdata"])
    return list(dict.fromkeys(roots))


def discover_wow(saved="", *, roots=None, max_directories=20000, max_seconds=8):
    """Bounded, read-only search suitable for a background worker.

    Skip assets and user-profile trees in Wine prefixes, and don't follow
    directory links (Wine's z: drive can point at the entire host filesystem).
    Explicit launcher roots and the saved installation may themselves be links.
    """
    found = []
    if saved:
        saved_path = Path(saved).expanduser()
        found.extend(wow_directories(saved_path))
        if saved_path.name.casefold() in WOW_CLIENTS:
            for sibling in wow_directories(saved_path.parent):
                if sibling not in found:
                    found.append(sibling)
    pending = deque(
        (Path(root), 0) for root in (wow_search_roots() if roots is None else roots)
    )
    seen = set()
    deadline = time.monotonic() + max_seconds
    skipped = {
        "data",
        "cache",
        "logs",
        "screenshots",
        "interface",
        "wtf",
        "windows",
        "users",
        "dosdevices",
        "node_modules",
        ".git",
    }
    while pending and len(seen) < max_directories and time.monotonic() < deadline:
        folder, depth = pending.popleft()
        try:
            folder = folder.resolve()
            if folder in seen:
                continue
            seen.add(folder)
            candidates = wow_directories(folder)
            if candidates:
                found.extend(
                    candidate for candidate in candidates if candidate not in found
                )
                continue
            if depth >= 9:
                continue
            with os.scandir(folder) as entries:
                for entry in entries:
                    if entry.name.casefold() in skipped:
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        pending.append((Path(entry.path), depth + 1))
                    if len(pending) >= max_directories:
                        break
        except (OSError, RuntimeError):
            continue
    return found


def install_addon(wow_dir: Path, backup_root: Path):
    wow_dir = wow_directory(wow_dir)
    if wow_dir is None:
        raise ValueError(
            "Choose a WoW client folder, such as _retail_, _classic_, or _classic_era_. If several versions are installed, select one first."
        )
    source = resources() / "addon/ThumbTalk"
    if not (source / "ThumbTalk.toc").is_file():
        raise ValueError(
            "The ThumbTalk addon is missing from this download. Download the complete app bundle."
        )
    target = wow_dir / "Interface/AddOns/ThumbTalk"
    if target.is_symlink() or (
        target.exists() and not (target / "ThumbTalk.toc").is_file()
    ):
        raise ValueError(
            "The target folder belongs to another addon or is a link. Choose a clean ThumbTalk addon folder."
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    previous = None
    if target.exists():
        backup_root.mkdir(parents=True, exist_ok=True)
        previous = (
            Path(tempfile.mkdtemp(prefix="addon-", dir=backup_root)) / "ThumbTalk"
        )
        shutil.copytree(target, previous)
    try:
        shutil.copytree(source, target, dirs_exist_ok=True)
    except Exception:
        if previous is not None:
            shutil.copytree(previous, target, dirs_exist_ok=True)
        raise
    return target


def remember_addon(config: Path, client: Path):
    """Keep every explicitly selected install, including custom library locations."""
    from thumbtalk_speech import read_config, write_config

    data = read_config(config)
    locations = data.get("addon_installs", [])
    if not isinstance(locations, list):
        locations = []
    locations = [value for value in locations if isinstance(value, str)]
    value = str(client.resolve())
    if value not in locations:
        locations.append(value)
    data["addon_installs"] = locations
    write_config(config, data)


def update_installed_addons(config: Path, *, roots=None):
    """Refresh existing ThumbTalk addons; never add it to a new client silently.

    Find legacy installs beside the saved client and in launcher libraries, plus
    all custom installs explicitly registered through setup. Keep an addon backup
    before replacing files. Refuse linked targets or unrelated addon metadata.
    """
    from thumbtalk_speech import read_config

    data = read_config(config)
    chat = data.get("chat", {})
    saved = chat.get("wow_dir", "") if isinstance(chat, dict) else ""
    clients = discover_wow(saved if isinstance(saved, str) else "", roots=roots)
    locations = data.get("addon_installs", [])
    if isinstance(locations, list):
        for location in locations:
            if isinstance(location, str):
                for client in wow_directories(Path(location)):
                    if client not in clients:
                        clients.append(client)
    source = resources() / "addon/ThumbTalk"
    files = [path for path in source.rglob("*") if path.is_file()]
    if not (source / "ThumbTalk.toc").is_file() or not files:
        raise ValueError("The bundled ThumbTalk addon is missing.")
    report = {"updated": [], "current": [], "errors": []}
    for client in clients:
        target = client / "Interface/AddOns/ThumbTalk"
        toc = target / "ThumbTalk.toc"
        if not toc.exists():
            continue
        try:
            if any(
                path.is_symlink()
                for path in (client / "Interface", target.parent, target)
            ) or any(path.is_symlink() for path in target.rglob("*")):
                raise ValueError("Linked addon files need a manual update in Setup.")
            metadata = toc.read_text(encoding="utf-8-sig")
            if not re.search(
                r"^## Title: ThumbTalk\s*$", metadata, re.MULTILINE
            ) or not re.search(
                r"^## Author: ThumbTalk contributors;", metadata, re.MULTILINE
            ):
                raise ValueError(
                    "This addon has different ownership metadata; update it manually in Setup."
                )
            if all(
                (target / path.relative_to(source)).is_file()
                and (target / path.relative_to(source)).read_bytes()
                == path.read_bytes()
                for path in files
            ):
                report["current"].append(str(client))
            else:
                install_addon(client, config.parent / "backups")
                report["updated"].append(str(client))
        except (OSError, ValueError) as error:
            report["errors"].append(f"{wow_label(client)} ({client}): {error}")
    return report
