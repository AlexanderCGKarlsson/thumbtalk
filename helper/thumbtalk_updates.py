"""On-demand checks against ThumbTalk's published GitHub Releases."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import certifi
from packaging.version import Version

from thumbtalk_version import VERSION

REPOSITORY = "AlexanderCGKarlsson/thumbtalk"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"
LATEST_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
PREVIEW_API = f"https://api.github.com/repos/{REPOSITORY}/releases?per_page=100"
TIMEOUT = 8
MAX_RESPONSE_BYTES = 1024 * 1024


@dataclass(frozen=True)
class UpdateResult:
    state: str
    message: str
    release_url: str = RELEASES_URL
    latest_version: str | None = None


def compare_release(payload, current_version, *, allow_preview=False):
    """Compare a published version tag, never arbitrary API links."""
    if not isinstance(payload, dict):
        raise ValueError("Invalid release response")
    if payload.get("draft") is not False or not isinstance(
        payload.get("prerelease"), bool
    ):
        raise ValueError("Expected a published release")
    if payload.get("prerelease") is not False and not allow_preview:
        raise ValueError("Expected a stable release")
    if not isinstance(payload.get("published_at"), str) or not payload["published_at"]:
        raise ValueError("Release has not been published")
    tag = payload.get("tag_name")
    if not isinstance(tag, str) or not re.fullmatch(
        r"v?[0-9][A-Za-z0-9.+_-]{0,63}", tag
    ):
        raise ValueError("Release tag is not a version")
    # Local build labels do not turn the same published version into an update.
    latest = Version(Version(tag).public)
    current = Version(Version(current_version).public)
    if (latest.is_prerelease or latest.is_devrelease) and not allow_preview:
        raise ValueError("Expected a stable version tag")
    channel = (
        "preview"
        if payload["prerelease"] or latest.is_prerelease or latest.major == 0
        else "stable release"
    )
    url = RELEASES_URL + "/tag/" + quote(tag, safe="")
    if latest > current:
        return UpdateResult(
            "available",
            f"ThumbTalk {latest} is available. Open the release page to download the update.",
            url,
            str(latest),
        )
    if latest == current:
        return UpdateResult(
            "up_to_date",
            f"You're up to date with the latest {channel} ({latest}).",
            url,
            str(latest),
        )
    return UpdateResult(
        "ahead",
        f"This build is newer than the latest {channel} ({latest}).",
        url,
        str(latest),
    )


def check_for_updates(current_version=VERSION, *, opener=None):
    """One bounded, anonymous HTTPS request; never installs or launches files."""
    preview = Version(current_version).major == 0
    no_release = UpdateResult(
        "no_release",
        "No published release is available for this update channel yet. Check GitHub Releases for news.",
    )
    request = Request(
        PREVIEW_API if preview else LATEST_API,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ThumbTalk/" + VERSION,
        },
    )
    try:
        context = ssl.create_default_context(cafile=certifi.where())
        with (opener or urlopen)(request, timeout=TIMEOUT, context=context) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError("Release response exceeds size limit")
        payload = json.loads(raw)
        if not preview:
            return compare_release(payload, current_version)
        if not isinstance(payload, list):
            raise ValueError("Expected a release list")
        candidates = []
        for item in payload:
            try:
                candidates.append(
                    compare_release(item, current_version, allow_preview=True)
                )
            except ValueError:
                continue  # Drafts, unpublished releases and non-version tags.
        return (
            max(candidates, key=lambda item: Version(item.latest_version))
            if candidates
            else no_release
        )
    except HTTPError as error:
        if error.code == 404:
            return no_release
        if error.code in (403, 429):
            return UpdateResult(
                "error",
                "GitHub is limiting update checks. Try again later or open GitHub Releases.",
            )
        return UpdateResult(
            "error", "GitHub could not complete the update check. Try again later."
        )
    except (URLError, OSError):
        return UpdateResult(
            "error", "Could not reach GitHub. Check your connection and try again."
        )
    except ValueError:
        return UpdateResult(
            "error",
            "Could not compare the release version. Open GitHub Releases to check manually.",
        )
