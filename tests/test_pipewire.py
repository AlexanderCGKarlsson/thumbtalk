import io
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helper"))
from thumbtalk_pipewire import CaptureStream, command, host_environment, raw_option
from thumbtalk_audio import managed_device


class PipeWireTests(unittest.TestCase):
    def test_host_commands_do_not_load_frozen_libraries(self):
        with patch.dict(
            "os.environ",
            {"LD_LIBRARY_PATH": "/bundle", "LD_LIBRARY_PATH_ORIG": "/host"},
        ):
            self.assertEqual(host_environment()["LD_LIBRARY_PATH"], "/host")
        with patch.dict("os.environ", {"LD_LIBRARY_PATH": "/bundle"}, clear=True):
            self.assertNotIn("LD_LIBRARY_PATH", host_environment())

    def test_raw_arguments_match_old_and_new_pipewire(self):
        for help_text in (
            b"pw-cat [<file>|-] --record --format",
            b"--record --raw --format",
        ):
            raw_option.cache_clear()
            with (
                patch(
                    "thumbtalk_pipewire.shutil.which", return_value="/usr/bin/pw-cat"
                ),
                patch(
                    "thumbtalk_pipewire.subprocess.run",
                    return_value=Mock(stdout=help_text),
                ),
            ):
                args = command("input")
                self.assertEqual("--raw" in args, b"--raw" in help_text)
                self.assertIn("--format=f32", args)
                self.assertIn("--channels=1", args)
                self.assertEqual(args[-1], "-")
        raw_option.cache_clear()

    def test_early_pipewire_uses_pulse_compatible_streaming(self):
        with (
            patch("thumbtalk_pipewire.raw_option", return_value=None),
            patch("thumbtalk_pipewire.shutil.which", return_value="/usr/bin/pacat"),
        ):
            args = command("input")
            self.assertEqual(args[0], "/usr/bin/pacat")
            self.assertIn("--format=float32le", args)
            self.assertIn("--record", args)
            self.assertIn("--raw", args)

    def test_capture_preserves_float_samples_across_partial_pipe_reads(self):
        samples = np.linspace(-0.3, 0.3, 101, dtype="<f4")

        class Chunks(io.BytesIO):
            def read(self, size=-1):
                return super().read(min(size, 13))

        blocks = []
        stream = CaptureStream(lambda data, *_: blocks.append(data.copy()))
        stream.process = Mock(stdout=Chunks(samples.tobytes()), stderr=io.BytesIO())
        stream.read()
        np.testing.assert_array_equal(np.concatenate(blocks).reshape(-1), samples)

    def test_managed_routes_require_enumerated_input_support(self):
        devices = [
            {"name": "Hardware", "max_input_channels": 2},
            {"name": "pipewire", "max_input_channels": 0},
            {"name": "pulse", "max_input_channels": 1},
        ]
        self.assertEqual(managed_device(devices), "pulse")
        self.assertIsNone(managed_device(devices[:1]))
