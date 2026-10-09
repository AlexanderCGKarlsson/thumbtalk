"""Microphone meter and discard behavior without opening a real device."""

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helper"))
from thumbtalk_runtime import Recorder


@unittest.skipUnless(importlib.util.find_spec("numpy"), "Audio dependencies")
class RecorderTests(unittest.TestCase):
    def test_meter_tracks_current_level_and_peak(self):
        import numpy as np

        recorder = Recorder()
        recorder.recording = True
        recorder.callback(np.array([[0.02], [-0.6]], dtype="float32"), 2, None, None)
        self.assertAlmostEqual(recorder.level, 0.6)
        recorder.callback(np.zeros((2, 1), dtype="float32"), 2, None, None)
        self.assertEqual(recorder.level, 0)
        self.assertAlmostEqual(recorder.peak, 0.6)
        self.assertEqual(len(recorder.stop()), 4)

    def test_microphone_check_discards_audio_without_resampling(self):
        import numpy as np

        recorder = Recorder()
        recorder.recording = True
        recorder.rate = 48000
        recorder.callback(np.ones((10, 1), dtype="float32"), 10, None, None)
        with patch.dict(sys.modules, {"av": None}):
            self.assertEqual(len(recorder.stop(discard=True)), 0)
        self.assertEqual(recorder.chunks, [])

    def test_audio_overflow_is_reported_without_dropping_valid_samples(self):
        import numpy as np

        recorder = Recorder()
        recorder.recording = True
        recorder.callback(
            np.ones((12, 1), dtype="float32"),
            12,
            None,
            SimpleNamespace(input_overflow=True),
        )
        self.assertEqual(recorder.overflows, 1)
        self.assertEqual(len(recorder.stop()), 12)
