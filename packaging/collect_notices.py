"""Retain wheel notices and record the exact environment used for each bundle.

This is an inventory, not a license compatibility decision. Missing wheel notices
are explicit so a maintainer can resolve them before publishing a release.
"""

from importlib.metadata import distributions
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import sys
import sysconfig


def notice_file(path):
    """Match notices, including British LICENCE, without copying source modules."""
    path = PurePosixPath(str(path).replace("\\", "/"))
    return (
        not path.is_absolute()
        and not PureWindowsPath(str(path)).drive
        and ".." not in path.parts
        and path.suffix.lower() not in (".py", ".pyc", ".pyo", ".so", ".dll")
        and any(
            re.match(r"^(licen[sc]e|copying|copyright|notice)(?:[._-]|$)", part, re.I)
            or part.lower() == "licenses"
            for part in path.parts
        )
    )


def collect_notices(destination, packages=None, python_candidates=None):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    inventory = []
    packages = distributions() if packages is None else packages
    for package in sorted(packages, key=lambda item: item.metadata["Name"].lower()):
        name = package.metadata["Name"]
        if name.lower().replace("_", "-") == "thumbtalk-helper":
            continue
        folder = re.sub(r"[^A-Za-z0-9._-]", "_", name + "-" + package.version)
        target = destination / folder
        target.mkdir(exist_ok=True)
        copied = []
        for entry in sorted(package.files or (), key=str):
            if not notice_file(entry):
                continue
            source = Path(package.locate_file(entry))
            if not source.is_file():
                continue
            relative = PurePosixPath(str(entry).replace("\\", "/"))
            output = target.joinpath(*relative.parts)
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, output)
            copied.append(output.relative_to(destination).as_posix())
        record = {
            "name": name,
            "version": package.version,
            "license": package.metadata.get("License-Expression")
            or package.metadata.get("License")
            or "Not declared in wheel metadata",
            "project_urls": package.metadata.get_all("Project-URL", []),
            "homepage": package.metadata.get("Home-page", ""),
            "notice_files": copied,
            "needs_notice_review": not copied,
        }
        (target / "metadata.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        inventory.append(record)

    if python_candidates is None:
        python_candidates = [
            Path(sysconfig.get_path("stdlib")) / "LICENSE.txt",
            Path(sys.base_prefix) / "LICENSE.txt",
            Path(sys.base_prefix) / "LICENSE",
        ]
    python_notices = []
    for candidate in python_candidates:
        candidate = Path(candidate)
        if candidate.is_file():
            target = destination / "Python"
            target.mkdir(exist_ok=True)
            shutil.copyfile(candidate, target / "LICENSE.txt")
            python_notices.append("Python/LICENSE.txt")
            break
    inventory.append(
        {
            "name": "Python",
            "version": sys.version.split()[0],
            "project_urls": ["Source, https://www.python.org/downloads/source/"],
            "notice_files": python_notices,
            "needs_notice_review": not python_notices,
        }
    )
    (destination / "inventory.json").write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    missing = [
        item["name"] + " " + item["version"]
        for item in inventory
        if item["needs_notice_review"]
    ]
    (destination / "README.txt").write_text(
        "ThumbTalk build-environment dependency notices\n\n"
        "Original wheel-provided license/notice files are retained unchanged.\n"
        "inventory.json records package versions, upstream URLs and missing notices.\n"
        "Build tools are included conservatively; this is not a list of loaded modules.\n"
        "Metadata is not a substitute for license texts or corresponding source.\n"
        "Review native libraries inside wheels separately before distribution.\n"
        "See ../THIRD_PARTY_NOTICES.md for attribution and current release blockers.\n\n"
        "No notice file provided by the installed package; review required:\n"
        + "".join("- " + name + "\n" for name in missing),
        encoding="utf-8",
    )
    return inventory
