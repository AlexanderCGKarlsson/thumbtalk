"""Local speech settings and transcription, independent of game input and audio devices."""

from __future__ import annotations

import json
import os
import platform
import re
import tempfile
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

# Whisper language tokens; names accepted by ThumbTalk are listed in LANGUAGE_ALIASES.
LANGUAGE_CODES = frozenset(
    """
af am ar as az ba be bg bn bo br bs ca cs cy da de el en es et eu fa fi fo fr gl
 gu ha haw he hi hr ht hu hy id is it ja jw ka kk km kn ko la lb ln lo lt lv mg mi
 mk ml mn mr ms mt my ne nl nn no oc pa pl ps pt ro ru sa sd si sk sl sn so sq sr
 su sv sw ta te tg th tk tl tr tt uk ur uz vi yi yo zh yue
""".split()
)
LANGUAGE_ALIASES = {
    "english": "en",
    "norwegian": "no",
    "norsk": "no",
    "nb": "no",
    "bokmål": "no",
    "bokmal": "no",
    "nynorsk": "nn",
    "swedish": "sv",
    "svenska": "sv",
    "danish": "da",
    "dansk": "da",
    "german": "de",
    "deutsch": "de",
    "french": "fr",
    "spanish": "es",
    "italian": "it",
    "portuguese": "pt",
    "dutch": "nl",
    "finnish": "fi",
    "polish": "pl",
    "ukrainian": "uk",
    "russian": "ru",
    "bulgarian": "bg",
    "japanese": "ja",
    "korean": "ko",
    "chinese": "zh",
    "arabic": "ar",
    "hindi": "hi",
}
LANGUAGE_NAMES = {
    "auto": "Automatic",
    "en": "English",
    "no": "Norwegian",
    "nn": "Norwegian Nynorsk",
    "sv": "Swedish",
    "da": "Danish",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "it": "Italian",
    "pt": "Portuguese",
    "fi": "Finnish",
    "pl": "Polish",
    "uk": "Ukrainian",
    "ja": "Japanese",
    "ko": "Korean",
    "zh": "Chinese",
    "ar": "Arabic",
}

PRESETS = {
    "economy": {"model": "tiny", "cpu_threads": 2},
    "balanced": {"model": "base", "cpu_threads": 2},
    "accuracy": {"model": "small", "cpu_threads": 2},
    "parakeet": {"model": "parakeet-v3", "cpu_threads": 2},
}


def normalize_language(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(
            "Language must be 'auto', a Whisper language code, or a supported name"
        )
    name = value.strip().lower().replace("_", "-")
    if name == "auto":
        return None
    code = LANGUAGE_ALIASES.get(name, name)
    # Common OS locale tags, including nb-NO and en-US.
    if "-" in code:
        code = code.split("-", 1)[0]
        code = LANGUAGE_ALIASES.get(code, code)
    if code not in LANGUAGE_CODES:
        raise ValueError(f"Unsupported speech language {value!r}; use --list-languages")
    return code


@dataclass(frozen=True)
class SpeechSettings:
    preset: str = "accuracy"
    model: str = "small"
    language: str | None = None
    device: str = "cpu"
    compute_type: str = "int8"
    cpu_threads: int = 2

    def validate(self) -> None:
        if self.preset not in PRESETS:
            raise ValueError("Unknown speech preset")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("Speech model must be a model name or local directory")
        normalize_language(self.language)
        if self.model == "parakeet-v3":
            if self.language is not None:
                raise ValueError(
                    "Parakeet detects its 25 supported languages automatically. Choose Automatic."
                )
            if self.device != "cpu" or self.compute_type != "int8":
                raise ValueError("Parakeet uses local CPU / int8 mode.")
        if self.device not in ("auto", "cpu", "cuda"):
            raise ValueError("Speech device must be auto, cpu, or cuda")
        if not isinstance(self.compute_type, str) or not self.compute_type.strip():
            raise ValueError("Compute type must be a nonempty string")
        if type(self.cpu_threads) is not int or self.cpu_threads < 1:
            raise ValueError("CPU threads must be a positive integer")
        if self.model.endswith(".en") and self.language != "en":
            raise ValueError(
                "English-only .en models require --language en; use a multilingual model for auto/other languages"
            )


def config_path() -> Path:
    if platform.system() == "Windows":
        base = Path(os.environ.get("APPDATA", str(Path.home() / "AppData/Roaming")))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    return base / "thumbtalk" / "settings.json"


def read_config(path: Path) -> dict:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(data, dict)
        or type(data.get("version")) is not int
        or data["version"] != 1
    ):
        raise ValueError("ThumbTalk settings must be a version 1 JSON object")
    speech = data.get("speech", {})
    if not isinstance(speech, dict):
        raise ValueError("The speech settings must be a JSON object")
    unknown = set(speech) - set(SpeechSettings.__dataclass_fields__)
    if unknown:
        raise ValueError("Unknown speech settings: " + ", ".join(sorted(unknown)))
    return data


def resolve_settings(path: Path, **overrides) -> SpeechSettings:
    saved = read_config(path).get("speech", {})
    preset = overrides.get("preset") or saved.get("preset", "accuracy")
    if not isinstance(preset, str) or preset not in PRESETS:
        raise ValueError("Unknown speech preset")
    values = asdict(SpeechSettings())
    values.update(PRESETS[preset])
    values.update(saved)
    # An explicitly selected preset resets model/threads, retaining language/device.
    if overrides.get("preset") is not None:
        values.update(PRESETS[preset])
    values.update({key: value for key, value in overrides.items() if value is not None})
    values["preset"] = preset
    values["language"] = normalize_language(values["language"])
    result = SpeechSettings(**values)
    result.validate()
    return result


def save_settings(path: Path, settings: SpeechSettings) -> None:
    settings.validate()
    data = read_config(path)
    data.update(version=1, speech=asdict(settings))
    write_config(path, data)


def write_config(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic replacement prevents interrupted writes from corrupting preferences.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".thumbtalk-",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def prepare_model(model, log, *, local_files_only=False):
    """Resolve cached models offline first; distinguish download from loading."""
    if Path(model).is_dir():
        return model
    from faster_whisper.utils import download_model

    log(f"Checking {model} model on this device…")
    try:
        path = download_model(model, local_files_only=True)
        if (Path(path) / "model.bin").is_file():
            return path
    except FileNotFoundError:
        pass
    if local_files_only:
        raise ValueError("Download this model in Setup → Voice before switching.")
    log(
        f"Downloading {model} speech model… Keep the internet connected. This is only needed once."
    )
    return download_model(model)


class ModelCache:
    """Keep one model warm across setup steps, with separate per-runtime settings."""

    def __init__(self):
        self.lock = threading.Lock()
        self.key = self.model = None

    def __call__(self, settings, log=print, *, local_files_only=False):
        key = (
            settings.model,
            settings.device,
            settings.compute_type,
            settings.cpu_threads,
        )
        if self.lock.locked():
            log("Waiting for the previous model to finish loading…")
        with self.lock:
            if key != self.key:
                self.key = self.model = None
            factory = (
                ParakeetTranscriber
                if settings.model == "parakeet-v3"
                else WhisperTranscriber
            )
            transcriber = factory(
                settings, log, model=self.model, local_files_only=local_files_only
            )
            self.model, self.key = transcriber.model, key
            return transcriber

    def clear(self):
        with self.lock:
            self.key = self.model = None


PARAKEET_LANGUAGES = tuple(
    "bg hr cs da nl en et fi fr de el hu it lv lt mt pl pt ro sk sl es sv ru uk".split()
)
PARAKEET_REPOSITORY = "csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8"
PARAKEET_REVISION = "2bda32ec70b097a55adaa07d9a7173915b43cc78"
PARAKEET_FILES = (
    "encoder.int8.onnx",
    "decoder.int8.onnx",
    "joiner.int8.onnx",
    "tokens.txt",
)


def prepare_parakeet(log, *, local_files_only=False):
    from huggingface_hub import hf_hub_download

    paths = {}
    for filename in PARAKEET_FILES:
        args = dict(
            repo_id=PARAKEET_REPOSITORY, filename=filename, revision=PARAKEET_REVISION
        )
        try:
            path = hf_hub_download(**args, local_files_only=True)
        except FileNotFoundError:
            if local_files_only:
                raise ValueError("Download Parakeet in Setup → Voice before switching.")
            log(f"Downloading Parakeet V3: {filename}… About 670 MB total, once.")
            path = hf_hub_download(**args)
        paths[filename] = path
    return paths


class ParakeetTranscriber:
    def __init__(self, settings, log=print, *, model=None, local_files_only=False):
        settings.validate()
        from types import SimpleNamespace
        import sherpa_onnx

        self.settings, self.log, self.last_language = settings, log, None
        if model is None:
            paths = prepare_parakeet(log, local_files_only=local_files_only)
            log("Loading Parakeet V3 into memory…")
            recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
                encoder=paths["encoder.int8.onnx"],
                decoder=paths["decoder.int8.onnx"],
                joiner=paths["joiner.int8.onnx"],
                tokens=paths["tokens.txt"],
                num_threads=settings.cpu_threads,
                provider="cpu",
                debug=False,
                decoding_method="greedy_search",
                model_type="nemo_transducer",
            )
            self.model = SimpleNamespace(
                recognizer=recognizer,
                supported_languages=PARAKEET_LANGUAGES,
                automatic_language=True,
            )
        else:
            self.model = model
            log("Reusing Parakeet V3 — already loaded.")

    def transcribe(self, audio):
        import numpy as np

        if isinstance(audio, (str, Path)):
            from faster_whisper.audio import decode_audio

            audio = decode_audio(str(audio), sampling_rate=16000)
        audio = np.asarray(audio, dtype="float32").reshape(-1)
        if audio.size < 4000:
            return ""
        stream = self.model.recognizer.create_stream()
        stream.accept_waveform(16000, audio)
        self.model.recognizer.decode_stream(stream)
        return re.sub(r"\s+", " ", stream.result.text).strip()


def create_transcriber(settings, log=print, *, local_files_only=False):
    factory = (
        ParakeetTranscriber if settings.model == "parakeet-v3" else WhisperTranscriber
    )
    return factory(settings, log, local_files_only=local_files_only)


class WhisperTranscriber:
    """Keep a local model warm; load no controller, desktop, or microphone libraries."""

    def __init__(
        self,
        settings: SpeechSettings,
        log: Callable[[str], None] = print,
        *,
        model=None,
        local_files_only=False,
    ):
        settings.validate()
        from faster_whisper import WhisperModel

        self.settings = settings
        self.log = log
        self.last_language = None
        if model is None:
            model_path = prepare_model(
                settings.model, log, local_files_only=local_files_only
            )
            log(f"Loading {settings.model} speech model into memory…")
            self.model = WhisperModel(
                model_path,
                device=settings.device,
                compute_type=settings.compute_type,
                cpu_threads=settings.cpu_threads,
                num_workers=1,
            )
        else:
            self.model = model
            log(f"Reusing {settings.model} speech model — already loaded.")
        supported = self.model.supported_languages
        if settings.language and settings.language not in supported:
            raise ValueError(
                f"Model {settings.model!r} does not support language {settings.language!r}"
            )
        if settings.language is None and len(supported) == 1:
            raise ValueError(
                "Automatic language detection requires a multilingual model; select --language en for English-only models"
            )

    def transcribe(self, audio) -> str:
        if not isinstance(audio, (str, Path)) and audio.size < 16000 // 4:
            return ""
        segments, info = self.model.transcribe(
            str(audio) if isinstance(audio, Path) else audio,
            language=self.settings.language,
            task="transcribe",
            beam_size=1,
            best_of=1,
            vad_filter=True,
            condition_on_previous_text=False,
            without_timestamps=True,
        )
        text = " ".join(segment.text.strip() for segment in segments)
        self.last_language = info.language
        self.log(f"Speech language: {info.language}")
        return re.sub(r"\s+", " ", text).strip()
