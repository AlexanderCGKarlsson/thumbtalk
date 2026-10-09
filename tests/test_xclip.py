"""Linux clipboard transactions do not infer game acknowledgement or retry sends."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helper"))
from thumbtalk_xclip import (
    XclipClipboard,
    xclip_command,
    PASTE_GRACE_SECONDS,
    CLIPBOARD_TIMEOUT_SECONDS,
)


class XclipTests(unittest.TestCase):
    def provider(self, previous=b"old", targets=b"UTF8_STRING\nTARGETS\n"):
        state = {"text": previous, "targets": targets, "writes": [], "calls": []}

        def run(args, **kwargs):
            state["calls"].append((args, kwargs))
            self.assertGreater(kwargs["timeout"], 0)
            self.assertLessEqual(kwargs["timeout"], CLIPBOARD_TIMEOUT_SECONDS)
            self.assertEqual(
                kwargs["stdout"],
                subprocess.DEVNULL if "-in" in args else subprocess.PIPE,
            )
            self.assertEqual(kwargs["env"]["DISPLAY"], ":92")
            if "-target" in args:
                data = state["targets"]
            elif "-in" in args:
                state["text"] = kwargs["input"]
                state["writes"].append(kwargs["input"])
                data = b""
            else:
                data = state["text"]
            return subprocess.CompletedProcess(args, 0, data)

        with patch(
            "thumbtalk_xclip.xclip_command",
            return_value=("/bundle/xclip", {"DISPLAY": ":92"}),
        ):
            result = XclipClipboard(run=run, sleep=Mock(), log=Mock())
        return result, state

    def test_one_way_review_restores_original_without_any_reply(self):
        from thumbtalk_review import packet

        clip, state = self.provider()
        wire = packet("/s " + "ø" * 126, "aabbccdd")
        token = clip.acquire_review(wire)
        self.assertEqual(state["text"], wire.encode())
        clip.arm(token)
        clip.wait_for_paste(token)
        clip.restore(token)
        self.assertEqual(state["writes"], [wire.encode(), b"old"])
        self.assertEqual(state["text"], b"old")

    def test_review_does_not_restore_over_a_newer_user_copy(self):
        from thumbtalk_review import packet

        clip, state = self.provider()
        token = clip.acquire_review(packet("/s hello", "aabbccdd"))
        state["text"] = b"user copied later"
        clip.restore(token)
        self.assertEqual(state["text"], b"user copied later")

    def test_review_packet_validation_does_not_relax_normal_message_limit(self):
        from thumbtalk_review import packet

        clip, state = self.provider()
        for value in [
            "",
            None,
            "TTREADY1:aabbccdd;",
            "TTREVIEW2:aabbccdd:00:00000000;extra",
        ]:
            with self.assertRaises(ValueError):
                clip.acquire_review(value)
        with self.assertRaises(ValueError):
            clip.acquire(packet("/s " + "ø" * 126, "aabbccdd"))
        self.assertEqual(state["writes"], [])

    def test_unicode_transfers_as_stdin_and_restores_plain_text(self):
        clip, state = self.provider()
        text = 's æøå 日本語 $(literal); "quotes"'
        token = clip.acquire(text)
        clip.arm(token)
        clip.wait_for_paste(token)
        clip.arm(token)
        clip.restore(token)
        self.assertEqual(state["writes"], [text.encode(), b"old"])
        clip.sleep.assert_called_once_with(PASTE_GRACE_SECONDS)
        for args, kwargs in state["calls"]:
            self.assertNotIn(text, args)
            self.assertNotIn("shell", kwargs)
        self.assertNotIn(text, str(clip.log.call_args_list))
        self.assertIsNone(clip.token)

    def test_changed_copy_rejects_submit_and_is_not_overwritten(self):
        clip, state = self.provider()
        token = clip.acquire("s hello")
        state["text"] = b"user copied later"
        with self.assertRaisesRegex(RuntimeError, "lease changed"):
            clip.arm(token)
        clip.restore(token)
        self.assertEqual(state["text"], b"user copied later")
        self.assertEqual(state["writes"], [b"s hello"])
        next_token = clip.acquire("s next explicit request")
        clip.restore(next_token)
        self.assertEqual(state["text"], b"user copied later")

    def test_restore_failure_does_not_change_submission_result_or_retry(self):
        clip, state = self.provider()
        token = clip.acquire("s private")
        clip.run = Mock(side_effect=subprocess.TimeoutExpired("xclip", 3))
        clip.restore(token)
        clip.run.assert_called_once()
        self.assertIsNone(clip.token)
        self.assertNotIn("private", str(clip.log.call_args_list))
        self.assertEqual(state["writes"], [b"s private"])

    def test_write_timeout_is_fatal_and_not_retried(self):
        clip, _ = self.provider()
        original = clip.run

        def run(args, **kwargs):
            if "-in" in args:
                raise subprocess.TimeoutExpired("xclip", 3)
            return original(args, **kwargs)

        clip.run = Mock(side_effect=run)
        with self.assertRaisesRegex(RuntimeError, "No automatic retry"):
            clip.acquire("s hello")
        self.assertIsNone(clip.token)
        self.assertEqual(
            sum("-in" in call.args[0] for call in clip.run.call_args_list), 1
        )

    def test_unreadable_previous_text_does_not_prevent_new_copy(self):
        clip, state = self.provider()
        original = clip.run

        def run(args, **kwargs):
            if "-target" in args:
                return subprocess.CompletedProcess(args, 1, b"")
            return original(args, **kwargs)

        clip.run = run
        token = clip.acquire("s hello")
        clip.restore(token)
        self.assertEqual(state["writes"], [b"s hello"])

    def test_publication_wait_reads_again_without_republishing(self):
        clip, state = self.provider()
        original = clip.run
        reads = iter([b"old owner", b"s hello"])

        def run(args, **kwargs):
            if "-out" in args and "-target" not in args:
                return subprocess.CompletedProcess(args, 0, next(reads))
            return original(args, **kwargs)

        clip.run = run
        clip._publish(b"s hello")
        self.assertEqual(state["writes"], [b"s hello"])
        clip.sleep.assert_called_once_with(0.01)

    def test_publication_deadline_never_retries_a_write(self):
        clip, state = self.provider()
        original = clip.run

        def run(args, **kwargs):
            if "-out" in args and "-target" not in args:
                return subprocess.CompletedProcess(args, 0, b"different owner")
            return original(args, **kwargs)

        clip.run = run
        with patch("thumbtalk_xclip.time.monotonic", side_effect=[0, 4]):
            with self.assertRaisesRegex(RuntimeError, "publication was not ready"):
                clip._publish(b"s hello")
        self.assertEqual(state["writes"], [b"s hello"])

    def test_nontext_previous_clipboard_is_not_requested_as_text(self):
        clip, state = self.provider(targets=b"image/png\nTARGETS\n")
        token = clip.acquire("s hello")
        clip.restore(token)
        self.assertEqual(len(state["calls"]), 3)
        self.assertEqual(state["text"], b"s hello")

    def test_close_restores_once_and_invalidates_old_tokens(self):
        clip, state = self.provider()
        token = clip.acquire("s hello")
        clip.close()
        clip.close()
        self.assertEqual(state["writes"], [b"s hello", b"old"])
        with self.assertRaisesRegex(RuntimeError, "closed"):
            clip.arm(token)
        with self.assertRaisesRegex(RuntimeError, "closed"):
            clip.acquire("s again")

    def test_invalid_input_and_overlapping_leases_do_not_publish(self):
        clip, state = self.provider()
        for value in ["", None, "x\n", "x\x7f", "å" * 128]:
            with self.assertRaises(ValueError):
                clip.acquire(value)
        self.assertEqual(state["writes"], [])
        clip.acquire("s first")
        with self.assertRaisesRegex(RuntimeError, "already active"):
            clip.acquire("s second")
        self.assertEqual(state["writes"], [b"s first"])

    def test_frozen_build_uses_bundled_tool_and_preserves_game_display(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "clipboard/xclip"
            binary.parent.mkdir()
            binary.touch()
            with (
                patch.dict(
                    os.environ,
                    {
                        "DISPLAY": ":92",
                        "QT_QPA_PLATFORM": "xcb",
                        "LD_LIBRARY_PATH": "wrong",
                    },
                ),
                patch.object(sys, "_MEIPASS", tmp, create=True),
                patch.object(sys, "frozen", True, create=True),
            ):
                command, env = xclip_command()
                self.assertEqual(command, str(binary))
                self.assertEqual(env["DISPLAY"], ":92")
                self.assertEqual(
                    env["LD_LIBRARY_PATH"], str(binary.parent) + os.pathsep + tmp
                )
                binary.unlink()
                with self.assertRaisesRegex(RuntimeError, "Bundled xclip is missing"):
                    xclip_command()

    def test_source_tool_uses_original_library_environment(self):
        with (
            patch.dict(
                os.environ,
                {
                    "DISPLAY": ":92",
                    "LD_LIBRARY_PATH": "private",
                    "LD_LIBRARY_PATH_ORIG": "original",
                },
            ),
            patch("thumbtalk_xclip.shutil.which", return_value="/usr/bin/xclip"),
        ):
            command, env = xclip_command()
            self.assertEqual(command, "/usr/bin/xclip")
            self.assertEqual(env["LD_LIBRARY_PATH"], "original")

    def test_no_display_never_guesses_steam_uid_or_display(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "No game X11 display"):
                xclip_command()
