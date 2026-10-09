"""Release checks are explicit, bounded and based on version numbers."""

import io
import json
import ssl
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helper"))
from thumbtalk_updates import (
    LATEST_API,
    PREVIEW_API,
    MAX_RESPONSE_BYTES,
    RELEASES_URL,
    TIMEOUT,
    check_for_updates,
    compare_release,
)
from thumbtalk_version import VERSION


def release(tag="v1.0.0", **changes):
    payload = dict(
        tag_name=tag,
        draft=False,
        prerelease=False,
        published_at="2026-10-09T12:00:00Z",
    )
    payload.update(changes)
    return payload


class UpdateTests(unittest.TestCase):
    def test_newer_equal_older_and_multi_digit_versions(self):
        for current, tag, state in (
            ("1.0.0rc1+test45", "v1.0.0", "available"),
            ("1.9.0", "v1.10.0", "available"),
            ("1.0.0", "v1.0.0", "up_to_date"),
            ("1.0.0+local", "v1.0.0", "up_to_date"),
            ("2.0.0rc2", "v1.9.0", "ahead"),
        ):
            with self.subTest(current=current, tag=tag):
                result = compare_release(release(tag), current)
                self.assertEqual(result.state, state)
                self.assertEqual(result.release_url, RELEASES_URL + "/tag/" + tag)

    def test_uses_fixed_repo_without_authentication_or_local_data(self):
        opener = Mock(return_value=io.BytesIO(json.dumps(release()).encode()))
        self.assertEqual(check_for_updates("1.0.0", opener=opener).state, "up_to_date")
        opener.assert_called_once()
        request = opener.call_args.args[0]
        self.assertEqual(request.full_url, LATEST_API)
        self.assertIsNone(request.data)
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(opener.call_args.kwargs["timeout"], TIMEOUT)
        context = opener.call_args.kwargs["context"]
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertNotIn("authorization", {k.lower() for k in request.headers})

    def test_ignores_api_html_and_asset_links(self):
        result = compare_release(
            release(
                html_url="https://example.org",
                assets=[{"browser_download_url": "file:///tmp/no"}],
            ),
            "0.9",
        )
        self.assertEqual(result.release_url, RELEASES_URL + "/tag/v1.0.0")

    def test_draft_prerelease_unpublished_invalid_or_unsafe_tags_are_rejected(self):
        cases = [
            [],
            {},
            release("latest"),
            release("v1.0.0rc2"),
            release("v1.0.0.dev1"),
            release("../../bad"),
            release("v1.0.0?x=1"),
            release("v1.0.0\n"),
        ]
        for change in (
            {"draft": True},
            {"prerelease": True},
            {"published_at": None},
            {"draft": "false"},
        ):
            item = release()
            item.update(change)
            cases.append(item)
        for payload in cases:
            with self.subTest(payload=payload):
                opener = Mock(return_value=io.BytesIO(json.dumps(payload).encode()))
                result = check_for_updates("1.0.0", opener=opener)
                self.assertEqual(result.state, "error")
                self.assertEqual(result.release_url, RELEASES_URL)

    def test_preview_checks_published_previews_and_stable_releases(self):
        payload = [
            release("v0.8.2", draft=True, prerelease=True),
            release("v0.8.1", prerelease=True),
            release("v0.8.0", prerelease=True),
            release("not-a-version"),
            release("v9.0.0", published_at=None),
        ]
        opener = Mock(return_value=io.BytesIO(json.dumps(payload).encode()))
        result = check_for_updates("0.8.0", opener=opener)
        self.assertEqual(opener.call_args.args[0].full_url, PREVIEW_API)
        self.assertEqual(result.state, "available")
        self.assertEqual(result.latest_version, "0.8.1")
        payload.append(release("v1.0.0"))
        result = check_for_updates(
            "0.8.0", opener=Mock(return_value=io.BytesIO(json.dumps(payload).encode()))
        )
        self.assertEqual(result.latest_version, "1.0.0")

    def test_preview_equal_version_is_not_called_stable(self):
        payload = [release("v0.8.0", prerelease=True)]
        result = check_for_updates(
            "0.8.0", opener=Mock(return_value=io.BytesIO(json.dumps(payload).encode()))
        )
        self.assertEqual(result.state, "up_to_date")
        self.assertIn("preview", result.message)
        self.assertNotIn("stable", result.message)

    def test_preview_empty_or_draft_only_list_has_no_published_release(self):
        for payload in ([], [release(draft=True)], [release(published_at=None)]):
            result = check_for_updates(
                "0.8.0",
                opener=Mock(return_value=io.BytesIO(json.dumps(payload).encode())),
            )
            self.assertEqual(result.state, "no_release")

    def test_stable_client_does_not_offer_a_preview(self):
        opener = Mock(
            return_value=io.BytesIO(
                json.dumps(release("v1.1.0", prerelease=True)).encode()
            )
        )
        self.assertEqual(check_for_updates("1.0.0", opener=opener).state, "error")
        self.assertEqual(opener.call_args.args[0].full_url, LATEST_API)

    def test_errors_are_not_reported_as_up_to_date_and_never_retry(self):
        errors = [(404, "no_release"), (403, "error"), (429, "error"), (500, "error")]
        for code, state in errors:
            opener = Mock(side_effect=HTTPError(LATEST_API, code, "error", {}, None))
            result = check_for_updates(opener=opener)
            self.assertEqual(result.state, state)
            opener.assert_called_once()
        for error in (TimeoutError(), URLError("offline"), OSError("TLS failure")):
            self.assertEqual(
                check_for_updates(opener=Mock(side_effect=error)).state, "error"
            )

    def test_malformed_and_oversized_responses(self):
        for raw in (b"not json", b"\xff", b" " * (MAX_RESPONSE_BYTES + 1)):
            self.assertEqual(
                check_for_updates(opener=Mock(return_value=io.BytesIO(raw))).state,
                "error",
            )

    def test_project_and_app_versions_agree(self):
        import re

        pyproject = (
            Path(__file__).resolve().parents[1] / "helper/pyproject.toml"
        ).read_text()
        self.assertEqual(
            re.search(r'^version = "([^"]+)"', pyproject, re.M)[1], VERSION
        )
        from thumbtalk_version import TEST_BUILD

        root = Path(__file__).resolve().parents[1]
        toc = (root / "addon/ThumbTalk/ThumbTalk.toc").read_text()
        self.assertIn(f"## Version: {VERSION}-build{TEST_BUILD}", toc)
        addon = (root / "addon/ThumbTalk/ThumbTalk.lua").read_text()
        self.assertIn(f'local ADDON_BUILD = "{TEST_BUILD}"', addon)
        for name in (
            "install.sh",
            "install.ps1",
            "Install-ThumbTalk.cmd",
            "Install-ThumbTalk.desktop",
        ):
            with self.subTest(installer=name):
                self.assertIn(
                    RELEASES_URL + f"/download/v{VERSION}/"
                    if name.startswith("Install-")
                    else RELEASES_URL + f"/download/v{VERSION}",
                    (root / name).read_text(),
                )
