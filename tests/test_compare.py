import io
import json
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helper"))
from thumbtalk_compare import Comparison, MAX_BYTES, read_request, worker_main
from thumbtalk_speech import ParakeetTranscriber, SpeechSettings, PRESETS


class ComparisonTests(unittest.TestCase):
    def test_invalid_cpu_comparisons_rejected_before_launch(self):
        for threads in (0, True, "4", -1, 65):
            with self.subTest(threads=threads), self.assertRaises(ValueError):
                Comparison(np.zeros(16000), "en", cpu_threads=threads)
        with self.assertRaises(ValueError):
            Comparison(np.zeros(16000), "en", compare_cpu=True)

    def test_cpu_worker_uses_selected_threads_without_download_or_lower_priority(self):
        payload = np.zeros(16000, dtype="<f4").tobytes()
        header = dict(
            preset="accuracy",
            language="no",
            size=len(payload),
            cpu_threads=4,
            compare_cpu=True,
        )
        request = io.BytesIO(json.dumps(header).encode() + b"\n" + payload)
        output = io.StringIO()
        engine = Mock()
        engine.transcribe.return_value = "Blåbær og smør."
        with (
            patch("sys.stdin", SimpleNamespace(buffer=request)),
            patch("sys.stdout", output),
            patch.dict("os.environ", {}),
            patch("thumbtalk_compare.lower_priority") as priority,
            patch("thumbtalk_compare.peak_memory_mib", return_value=500),
            patch(
                "thumbtalk_compare.time.perf_counter",
                side_effect=[10, 12, 20, 24, 30, 33, 40, 43],
            ),
            patch(
                "thumbtalk_speech.create_transcriber", return_value=engine
            ) as factory,
        ):
            worker_main()
        priority.assert_not_called()
        settings = factory.call_args.args[0]
        self.assertEqual(
            (settings.model, settings.cpu_threads, settings.language),
            ("small", 4, "no"),
        )
        self.assertTrue(factory.call_args.kwargs["local_files_only"])
        self.assertEqual(engine.transcribe.call_count, 3)
        result = json.loads(output.getvalue().splitlines()[-1])
        self.assertEqual(result["first_seconds"], 4)
        self.assertEqual(result["warm_seconds"], 3)
        self.assertEqual(result["load_seconds"], 2)
        self.assertEqual(result["cpu_threads"], 4)
        self.assertTrue(result["consistent"])

    def test_cpu_comparison_keeps_audio_model_and_language_identical(self):
        payloads, environments, processes = [], [], []

        class Input(io.BytesIO):
            def close(self):
                if not self.closed:
                    payloads.append(self.getvalue())
                super().close()

        def launch(*args, **kwargs):
            if processes:
                processes[-1].wait.assert_called_once()
            environments.append(kwargs["env"])
            process = Mock(stdin=Input(), stdout=io.BytesIO(b'{"kind":"result"}\n'))
            process.poll.return_value = 0
            processes.append(process)
            return process

        comparison = Comparison(
            np.linspace(-1, 1, 8000), "no", presets=["accuracy"], compare_cpu=True
        )
        original = comparison.payload
        with patch("thumbtalk_compare.subprocess.Popen", side_effect=launch):
            comparison.run()
        self.assertEqual(len(processes), 2)
        for threads, payload, env in zip((2, 4), payloads, environments):
            header, audio = payload.split(b"\n", 1)
            header = json.loads(header)
            self.assertEqual(audio, original)
            self.assertEqual(
                (header["preset"], header["language"], header["cpu_threads"]),
                ("accuracy", "no", threads),
            )
            self.assertEqual(env["OMP_NUM_THREADS"], str(threads))
            self.assertEqual(env["MKL_NUM_THREADS"], str(threads))
        self.assertEqual(comparison.payload, b"")

    def test_protocol_rejects_oversized_short_and_truncated_audio(self):
        for size in (False, -1, 0, 4, MAX_BYTES + 4, 16001, 16000):
            with self.subTest(size=size), self.assertRaises(ValueError):
                read_request(
                    io.BytesIO(
                        json.dumps(dict(preset="balanced", size=size)).encode() + b"\n"
                    )
                )

    def test_protocol_keeps_exact_samples(self):
        payload = np.linspace(-0.5, 0.5, 16000, dtype="<f4").tobytes()
        header, actual = read_request(
            io.BytesIO(
                json.dumps(dict(preset="parakeet", size=len(payload))).encode()
                + b"\n"
                + payload
            )
        )
        self.assertEqual(actual, payload)
        self.assertEqual(header["preset"], "parakeet")

    def test_cancel_before_start_never_launches_worker(self):
        comparison = Comparison(np.zeros(16000), "sv")
        comparison.cancel()
        with patch("thumbtalk_compare.subprocess.Popen") as launch:
            comparison.run()
        launch.assert_not_called()
        self.assertEqual(comparison.payload, b"")
        self.assertTrue(comparison.events.get()["cancelled"])

    def test_workers_reaped_before_next_and_use_identical_audio(self):
        processes = []
        payloads = []

        class Input(io.BytesIO):
            def close(self):
                if not self.closed:
                    payloads.append(self.getvalue())
                super().close()

        def launch(*args, **kwargs):
            if processes:
                processes[-1].wait.assert_called_once()
            process = Mock(stdin=Input(), stdout=io.BytesIO(b'{"kind":"result"}\n'))
            process.poll.return_value = 0
            processes.append(process)
            return process

        comparison = Comparison(np.linspace(-1, 1, 8000), "sv")
        expected = comparison.payload
        with patch("thumbtalk_compare.subprocess.Popen", side_effect=launch):
            comparison.run()
        self.assertEqual(len(processes), 3)
        self.assertTrue(all(item.split(b"\n", 1)[1] == expected for item in payloads))
        self.assertTrue(
            all(
                json.loads(item.split(b"\n", 1)[0])["language"] == "sv"
                for item in payloads
            )
        )

    def test_cancel_running_worker_kills_and_prevents_next(self):
        comparison = Comparison(np.zeros(16000), None)
        process = Mock(stdin=io.BytesIO(), stdout=io.BytesIO(b""))
        process.poll.return_value = None

        def launch(*args, **kwargs):
            comparison.cancelled.set()
            return process

        with patch("thumbtalk_compare.subprocess.Popen", side_effect=launch) as start:
            comparison.run()
        start.assert_called_once()
        process.kill.assert_called_once()
        process.wait.assert_called_once()

    def test_parakeet_uses_cpu_threads_and_automatic_language(self):
        settings = SpeechSettings(preset="parakeet", **PRESETS["parakeet"])
        recognizer = Mock()
        recognizer.create_stream.return_value = SimpleNamespace(
            accept_waveform=Mock(),
            result=SimpleNamespace(text=" Ska vi äta på McDonalds? "),
        )
        fake = SimpleNamespace(
            OfflineRecognizer=SimpleNamespace(
                from_transducer=Mock(return_value=recognizer)
            )
        )
        with (
            patch.dict(sys.modules, {"sherpa_onnx": fake}),
            patch(
                "thumbtalk_speech.prepare_parakeet",
                return_value={
                    n: n
                    for n in (
                        "encoder.int8.onnx",
                        "decoder.int8.onnx",
                        "joiner.int8.onnx",
                        "tokens.txt",
                    )
                },
            ),
        ):
            engine = ParakeetTranscriber(settings, log=lambda s: None)
            self.assertEqual(
                engine.transcribe(np.zeros(16000)), "Ska vi äta på McDonalds?"
            )
        self.assertEqual(
            fake.OfflineRecognizer.from_transducer.call_args.kwargs["num_threads"], 2
        )
        self.assertEqual(
            fake.OfflineRecognizer.from_transducer.call_args.kwargs["provider"], "cpu"
        )
        self.assertIn("sv", engine.model.supported_languages)
        self.assertNotIn("no", engine.model.supported_languages)
        with self.assertRaises(ValueError):
            SpeechSettings(
                preset="parakeet", model="parakeet-v3", language="sv"
            ).validate()
        self.assertTrue(all(preset["cpu_threads"] == 2 for preset in PRESETS.values()))
