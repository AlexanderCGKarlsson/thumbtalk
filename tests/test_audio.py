"""Device-rate regressions using real resampling, without physical audio devices."""

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helper"))
from thumbtalk_audio import audio_stats, input_warning, play_recording, resample_mono
from thumbtalk_runtime import Recorder


class AudioTests(unittest.TestCase):
    def tone(self, rate, seconds=1, amplitude=0.2):
        return (
            amplitude * np.sin(2 * np.pi * 440 * np.arange(int(rate * seconds)) / rate)
        ).astype("float32")

    def test_playback_resamples_16k_to_native_48k_stereo(self):
        audio = self.tone(16000)

        def check(**kwargs):
            self.assertEqual(kwargs["samplerate"], 48000)
            self.assertEqual(kwargs["channels"], 2)

        with (
            patch(
                "sounddevice.query_devices",
                return_value={"max_output_channels": 2, "default_samplerate": 48000},
            ),
            patch("sounddevice.check_output_settings", side_effect=check),
            patch("sounddevice.play") as play,
        ):
            seconds = play_recording(audio, "Speakers")
        output = play.call_args.args[0]
        self.assertEqual(play.call_args.args[1], 48000)
        self.assertEqual(output.shape, (48000, 2))
        np.testing.assert_array_equal(output[:, 0], output[:, 1])
        self.assertAlmostEqual(seconds, 1)
        self.assertLess(float(np.max(np.abs(output))), 0.21)
        self.assertEqual(play.call_args.kwargs["device"], "Speakers")
        frequency = np.argmax(np.abs(np.fft.rfft(output[:, 0])))
        self.assertEqual(frequency, 440)

    def test_output_open_failure_retries_and_stops_failed_stream(self):
        import sounddevice as sd

        with (
            patch(
                "sounddevice.query_devices",
                return_value={"max_output_channels": 2, "default_samplerate": 48000},
            ),
            patch("sounddevice.check_output_settings"),
            patch(
                "sounddevice.play",
                side_effect=[sd.PortAudioError("Invalid sample rate"), None],
            ) as play,
            patch("sounddevice.stop") as stop,
        ):
            self.assertAlmostEqual(play_recording(self.tone(16000)), 1)
        self.assertEqual(play.call_count, 2)
        stop.assert_called_once()
        self.assertEqual(play.call_args.args[0].ndim, 1)

    def test_capture_prefers_native_rate_even_if_16k_is_advertised(self):
        stream = Mock()

        def opened(**kwargs):
            stream.start.side_effect = lambda: kwargs["callback"](
                np.zeros((480, 2), dtype="float32"), 480, None, None
            )
            return stream

        with (
            patch(
                "sounddevice.query_devices",
                return_value={"max_input_channels": 2, "default_samplerate": 48000},
            ),
            patch("sounddevice.check_input_settings"),
            patch("sounddevice.InputStream", side_effect=opened) as create,
        ):
            recorder = Recorder("Microphone")
            recorder.start()
        self.assertEqual(create.call_args.kwargs["samplerate"], 48000)
        self.assertEqual(create.call_args.kwargs["channels"], 2)
        self.assertEqual(create.call_args.kwargs["blocksize"], 0)
        self.assertEqual(create.call_args.kwargs["latency"], "high")
        tone = self.tone(48000)
        recorder.callback(
            np.column_stack([np.zeros_like(tone), tone]), len(tone), None, None
        )
        result = recorder.stop()
        self.assertEqual(len(result), 16000)
        self.assertEqual(recorder.selected_channel, 2)
        self.assertLess(np.max(np.abs(result)), 0.21)
        self.assertAlmostEqual(
            float(np.sqrt(np.mean(result**2))), 0.2 / np.sqrt(2), places=3
        )
        stream.close.assert_called_once()

    def test_opposite_phase_input_channels_do_not_cancel_speech(self):
        recorder = Recorder()
        recorder.recording = True
        recorder.rate = 48000
        tone = self.tone(48000)
        recorder.callback(np.column_stack([tone, -tone]), len(tone), None, None)
        self.assertGreater(np.max(np.abs(recorder.stop())), 0.19)

    def test_native_rate_conversion_preserves_duration_and_volume(self):
        for rate in (44100, 48000):
            with self.subTest(rate=rate):
                converted = resample_mono(self.tone(rate), rate, 16000)
                self.assertEqual(len(converted), 16000)
                self.assertLess(np.max(np.abs(converted)), 0.21)
                self.assertEqual(np.argmax(np.abs(np.fft.rfft(converted))), 440)

    def test_single_peak_does_not_label_normal_speech_distorted(self):
        samples = self.tone(16000)
        samples[0] = 1
        self.assertEqual(input_warning(audio_stats(samples)), "")
        samples[:1000] = 1
        self.assertIn("distorted", input_warning(audio_stats(samples)))
        self.assertNotIn("lower", input_warning(audio_stats(samples)))

    def test_invalid_audio_is_not_played_or_sent_to_speech(self):
        samples = self.tone(16000)
        samples[42] = np.nan
        with patch("sounddevice.play") as play, self.assertRaises(ValueError):
            play_recording(samples)
        play.assert_not_called()
        recorder = Recorder()
        recorder.recording = True
        recorder.callback(samples.reshape(-1, 1), len(samples), None, None)
        with self.assertRaises(ValueError):
            recorder.stop()

    def test_prepared_microphone_reuses_stream_and_discards_idle_audio(self):
        stream = Mock()

        def opened(**kwargs):
            stream.start.side_effect = lambda: kwargs["callback"](
                np.ones((960, 2), dtype="float32"), 960, None, None
            )
            return stream

        recorder = Recorder("Mic")
        with (
            patch(
                "sounddevice.query_devices",
                return_value={"max_input_channels": 2, "default_samplerate": 48000},
            ),
            patch("sounddevice.check_input_settings"),
            patch("sounddevice.InputStream", side_effect=opened) as factory,
        ):
            recorder.prepare()
            self.assertEqual(recorder.chunks, [])
            self.assertFalse(recorder.recording)
            for _ in range(2):
                recorder.start()
                tone = self.tone(48000, seconds=0.1)
                recorder.callback(np.column_stack([tone, tone]), len(tone), None, None)
                result = recorder.stop()
                self.assertEqual(len(result), 1600)
                self.assertGreater(np.max(np.abs(result)), 0.19)
                recorder.callback(np.ones((960, 2), dtype="float32"), 960, None, None)
                self.assertEqual(recorder.chunks, [])
            factory.assert_called_once()
            stream.start.assert_called_once()
            stream.close.assert_not_called()
            recorder.close()
            stream.close.assert_called_once()
            with self.assertRaises(ValueError):
                recorder.prepare()

    def test_microphone_cannot_report_ready_before_audio_arrives(self):
        recorder = Recorder()
        stream = Mock()
        with (
            patch(
                "sounddevice.query_devices",
                return_value={"max_input_channels": 1, "default_samplerate": 48000},
            ),
            patch("sounddevice.check_input_settings"),
            patch("sounddevice.InputStream", return_value=stream),
            patch.object(recorder.first_audio, "wait", return_value=False),
            self.assertRaisesRegex(ValueError, "no audio arrived"),
        ):
            recorder.prepare()
        stream.close.assert_called_once()
        self.assertIsNone(recorder.stream)
