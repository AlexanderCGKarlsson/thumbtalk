"""Device-rate audio I/O; speech engines always receive mono float32 at 16 kHz."""

from __future__ import annotations

import numpy as np

SPEECH_RATE = 16000
_pipewire_playback = None


def stop_audio():
    global _pipewire_playback
    if _pipewire_playback:
        _pipewire_playback.stop()
        _pipewire_playback = None
    import sounddevice as sd

    sd.stop()


def resample_mono(audio, source_rate, target_rate):
    samples = np.ascontiguousarray(audio, dtype="float32").reshape(-1)
    if not samples.size or source_rate == target_rate:
        return samples.copy()
    import av

    frame = av.AudioFrame.from_ndarray(
        samples.reshape(1, -1), format="flt", layout="mono"
    )
    frame.sample_rate = int(source_rate)
    converter = av.AudioResampler(format="flt", layout="mono", rate=int(target_rate))
    frames = converter.resample(frame) + converter.resample(None)
    return np.concatenate([part.to_ndarray().reshape(-1) for part in frames]).astype(
        "float32", copy=False
    )


def device_formats(device, direction):
    """Prefer the endpoint's native rate over driver-provided 16 kHz conversion."""
    import sounddevice as sd

    info = sd.query_devices(device, direction)
    maximum = int(info["max_" + direction + "_channels"])
    if maximum < 1:
        raise ValueError(f"This device has no {direction} channels.")
    native = int(info.get("default_samplerate") or 48000)
    rates = tuple(dict.fromkeys((native, 48000, 44100, SPEECH_RATE)))
    # Stereo is native on many handheld endpoints. Never exceed two channels.
    channels = (2, 1) if maximum >= 2 else (1,)
    check = (
        sd.check_input_settings if direction == "input" else sd.check_output_settings
    )
    for rate in rates:
        for count in channels:
            try:
                check(device=device, samplerate=rate, channels=count, dtype="float32")
            except sd.PortAudioError:
                continue
            yield rate, count


def play_recording(audio, device=None):
    """Resample the speech buffer for the speakers; return its actual duration."""
    import sounddevice as sd

    samples = np.asarray(audio, dtype="float32").reshape(-1)
    if not samples.size:
        return 0.0
    if not np.isfinite(samples).all():
        raise ValueError(
            "The recording contains invalid samples. Choose another microphone and record again."
        )
    from thumbtalk_pipewire import SYSTEM_AUDIO, Playback

    if device == SYSTEM_AUDIO:
        global _pipewire_playback
        stop_audio()
        output = np.clip(resample_mono(samples, SPEECH_RATE, 48000), -1.0, 1.0)
        _pipewire_playback = Playback(output)
        return len(output) / 48000 + 0.3
    last_error = None
    for rate, channels in device_formats(device, "output"):
        output = resample_mono(samples, SPEECH_RATE, rate)
        # Protect the output from out-of-range microphone samples. Speech data is unchanged.
        output = np.clip(output, -1.0, 1.0)
        if channels == 2:
            output = np.repeat(output[:, None], 2, axis=1)
        try:
            sd.play(output, rate, device=device, blocking=False)
            return len(output) / rate
        except sd.PortAudioError as error:
            sd.stop()
            last_error = error
    raise ValueError(
        "Could not open this output at a supported rate. Choose another listening device."
        + (f" {last_error}" if last_error else "")
    )


def audio_stats(audio):
    samples = np.asarray(audio, dtype="float32").reshape(-1)
    if not samples.size:
        return {"rms": 0.0, "peak": 0.0, "clipped_fraction": 0.0, "invalid": False}
    finite = bool(np.isfinite(samples).all())
    if not finite:
        return {"rms": 0.0, "peak": 0.0, "clipped_fraction": 0.0, "invalid": True}
    return {
        "rms": float(np.sqrt(np.mean(samples.astype("float64") ** 2))),
        "peak": float(np.max(np.abs(samples))),
        "clipped_fraction": float(np.mean(np.abs(samples) >= 0.99)),
        "invalid": False,
    }


def input_warning(stats):
    if stats["invalid"]:
        return (
            "The microphone returned invalid audio. Choose another input and try again."
        )
    if stats["clipped_fraction"] >= 0.01:
        return f"Input may be distorted ({stats['clipped_fraction']:.0%} at the limit). Use the system microphone route if available, then check its recording level in Sound settings."
    if stats["rms"] < 0.003:
        return "Very little audio was captured. Listen back and check the selected microphone and mute setting."
    return ""


def managed_device(devices, direction="input"):
    """Prefer an explicitly enumerated sound-server route over direct ALSA hardware."""
    candidates = {
        d["name"].lower(): d["name"]
        for d in devices
        if d.get("max_" + direction + "_channels", 0)
    }
    return next(
        (
            candidates[name]
            for name in ("pipewire", "pulse", "default")
            if name in candidates
        ),
        None,
    )


def playback_error():
    return _pipewire_playback.error if _pipewire_playback else ""
