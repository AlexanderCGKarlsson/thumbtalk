import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helper"))
from thumbtalk_models import ModelDownloads, model_downloaded


class ModelsTests(unittest.TestCase):
    def test_only_complete_cached_models_are_available_without_network(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "faster_whisper.utils.download_model", return_value=directory
            ) as download,
        ):
            p = Path(directory)
            (p / "model.bin").write_bytes(b"weights")
            self.assertFalse(model_downloaded("accuracy"))
            (p / "config.json").write_text("{}")
            (p / "tokenizer.json").write_text("{}")
            self.assertTrue(model_downloaded("accuracy"))
            self.assertTrue(
                all(c.kwargs["local_files_only"] for c in download.call_args_list)
            )

    def test_downloads_all_checked_models_once_and_no_unchecked_models(self):
        cached = set()

        def whisper(model, log):
            self.assertEqual(model, "small")
            cached.add("accuracy")

        def parakeet(log):
            cached.add("parakeet")

        job = ModelDownloads(["accuracy", "parakeet", "accuracy"])
        with (
            patch(
                "thumbtalk_models.model_downloaded", side_effect=lambda p: p in cached
            ),
            patch("thumbtalk_models.prepare_model", side_effect=whisper) as w,
            patch("thumbtalk_models.prepare_parakeet", side_effect=parakeet) as p,
        ):
            job.run()
        w.assert_called_once()
        p.assert_called_once()
        events = list(job.events.queue)
        self.assertEqual(
            [value for kind, value in events if kind == "ready"],
            ["accuracy", "parakeet"],
        )
        self.assertEqual(events[-1], ("done", None))

    def test_cached_and_cancelled_downloads_never_fetch(self):
        for cancelled in (False, True):
            job = ModelDownloads(["accuracy"])
            if cancelled:
                job.cancelled.set()
            with (
                patch("thumbtalk_models.model_downloaded", return_value=True),
                patch("thumbtalk_models.prepare_model") as fetch,
            ):
                job.run()
            fetch.assert_not_called()

    def test_failed_download_does_not_mark_ready_and_finishes(self):
        job = ModelDownloads(["accuracy"])
        with (
            patch("thumbtalk_models.model_downloaded", return_value=False),
            patch("thumbtalk_models.prepare_model", side_effect=OSError("offline")),
        ):
            job.run()
        events = list(job.events.queue)
        self.assertFalse(any(kind == "ready" for kind, _ in events))
        self.assertTrue(
            any(kind == "error" and "offline" in value for kind, value in events)
        )
        self.assertEqual(events[-1], ("done", None))
