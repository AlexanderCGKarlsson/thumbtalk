"""On-disk speech models, downloaded explicitly without loading inference engines."""

from pathlib import Path
from queue import Queue
from threading import Event, Thread

from thumbtalk_speech import (
    PRESETS,
    PARAKEET_FILES,
    PARAKEET_REPOSITORY,
    PARAKEET_REVISION,
    prepare_model,
    prepare_parakeet,
)

CATALOG = {
    "accuracy": ("Whisper Small", "Recommended · multilingual"),
    "parakeet": ("Parakeet V3", "Optional · automatic language detection"),
    "balanced": ("Whisper Base", "Smaller download · lower accuracy"),
    "economy": ("Whisper Tiny", "Smallest download · lower accuracy"),
}


def model_downloaded(preset):
    """Check complete cached files only. Never contact the network or load weights."""
    if preset not in CATALOG:
        return False
    try:
        if preset == "parakeet":
            from huggingface_hub import hf_hub_download

            files = [
                hf_hub_download(
                    repo_id=PARAKEET_REPOSITORY,
                    revision=PARAKEET_REVISION,
                    filename=name,
                    local_files_only=True,
                )
                for name in PARAKEET_FILES
            ]
        else:
            from faster_whisper.utils import download_model

            directory = Path(
                download_model(PRESETS[preset]["model"], local_files_only=True)
            )
            files = [
                directory / name
                for name in ("model.bin", "config.json", "tokenizer.json")
            ]
        return all(
            Path(file).is_file() and Path(file).stat().st_size > 0 for file in files
        )
    except (OSError, ValueError):
        return False


class ModelDownloads:
    def __init__(self, presets):
        self.presets = tuple(dict.fromkeys(presets))
        if not self.presets or any(preset not in CATALOG for preset in self.presets):
            raise ValueError("Tick at least one speech model to download.")
        self.events = Queue()
        self.cancelled = Event()
        self.thread = Thread(target=self.run, daemon=True)

    def start(self):
        self.thread.start()

    def run(self):
        try:
            for preset in self.presets:
                if self.cancelled.is_set():
                    break
                self.events.put(("progress", "Preparing " + CATALOG[preset][0] + "…"))

                def progress(message):
                    if self.cancelled.is_set():
                        raise InterruptedError("Download stopped.")
                    self.events.put(("progress", message))

                try:
                    if not model_downloaded(preset):
                        if preset == "parakeet":
                            prepare_parakeet(progress)
                        else:
                            prepare_model(PRESETS[preset]["model"], progress)
                    if not model_downloaded(preset):
                        raise ValueError(
                            "The download is incomplete. Try downloading again."
                        )
                    self.events.put(("ready", preset))
                except Exception as error:
                    self.events.put(("error", CATALOG[preset][0] + ": " + str(error)))
        finally:
            self.events.put(("done", None))
