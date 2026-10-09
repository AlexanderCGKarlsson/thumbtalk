#!/usr/bin/env python3
"""ThumbTalk entry point. Settings and diagnostics do not import input libraries."""

from __future__ import annotations

import argparse
import json
import platform
import time
from dataclasses import asdict, replace
from pathlib import Path

from thumbtalk_speech import (
    LANGUAGE_ALIASES,
    LANGUAGE_CODES,
    PRESETS,
    create_transcriber,
    config_path,
    resolve_settings,
    save_settings,
)
from thumbtalk_chat import CHANNELS, load_chat
from thumbtalk_fonts import configure_bundled_fonts

from thumbtalk_version import VERSION, TEST_BUILD


def main():
    configure_bundled_fonts()
    parser = argparse.ArgumentParser(
        description="ThumbTalk — hold, speak, review, send. Free local dictation for WoW."
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"ThumbTalk {VERSION} · build {TEST_BUILD}",
    )
    parser.add_argument("--config", type=Path, default=config_path())
    parser.add_argument(
        "--language", help="auto, a language name/code, or locale such as nb-NO"
    )
    parser.add_argument("--list-languages", action="store_true")
    parser.add_argument("--speech-preset", choices=PRESETS)
    parser.add_argument("--model")
    parser.add_argument("--device", choices=("cpu", "auto", "cuda"))
    parser.add_argument("--compute-type")
    parser.add_argument("--cpu-threads", type=int)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--save-speech-settings", action="store_true")
    modes.add_argument("--transcribe-file", type=Path)
    modes.add_argument(
        "--update-addons",
        action="store_true",
        help="Refresh existing ThumbTalk addons after an app update",
    )
    modes.add_argument(
        "--check",
        action="store_true",
        help="Report configuration and platform without recording or typing",
    )
    modes.add_argument(
        "--headless",
        action="store_true",
        help="Run the game companion and its small status overlay",
    )
    modes.add_argument(
        "--startup",
        action="store_true",
        help="Start the Windows companion minimized using saved settings",
    )
    parser.add_argument(
        "--clipboard-worker", action="store_true", help=argparse.SUPPRESS
    )
    parser.add_argument("--compare-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--self-test", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--setup", action="store_true", help="Open guided setup (the default)"
    )
    parser.add_argument(
        "--practice",
        action="store_true",
        help="Try recording without sending to a game",
    )
    parser.add_argument("--input-device")
    parser.add_argument("--wow-dir")
    parser.add_argument("--channel", choices=CHANNELS)
    parser.add_argument("--recipient")
    args = parser.parse_args()
    if args.startup and platform.system() != "Windows":
        parser.error("--startup is only available on Windows")
    if args.clipboard_worker:
        from thumbtalk_clipboard import worker_main

        worker_main()
        return
    if args.compare_worker:
        from thumbtalk_compare import worker_main

        worker_main()
        return
    if args.self_test:
        from thumbtalk_selftest import run

        run()
        return
    if args.list_languages:
        print(
            "auto = automatic detection; language codes: "
            + " ".join(sorted(LANGUAGE_CODES))
        )
        print(
            "Accepted names: "
            + ", ".join(
                f"{name}={code}" for name, code in sorted(LANGUAGE_ALIASES.items())
            )
        )
        return
    try:
        if args.update_addons:
            from thumbtalk_setup import update_installed_addons

            report = update_installed_addons(args.config)
            print(
                f"ThumbTalk addons: {len(report['updated'])} updated, {len(report['current'])} already current."
            )
            if report["updated"]:
                print("Restart WoW to load the updated addon.")
            if report["errors"]:
                for error in report["errors"]:
                    print(error)
                raise RuntimeError(
                    "Some addons need an update from ThumbTalk Setup > WoW."
                )
            return
        speech = resolve_settings(
            args.config,
            preset=args.speech_preset,
            model=args.model,
            language=args.language,
            device=args.device,
            compute_type=args.compute_type,
            cpu_threads=args.cpu_threads,
        )
        if args.save_speech_settings:
            save_settings(args.config, speech)
            print(f"Saved speech preferences to {args.config}")
            return
        if args.transcribe_file:
            if not args.transcribe_file.is_file():
                parser.error(f"Audio file does not exist: {args.transcribe_file}")
            started = time.monotonic()
            transcriber = create_transcriber(speech)
            loaded = time.monotonic()
            print(transcriber.transcribe(args.transcribe_file))
            print(
                f"Model load: {loaded - started:.2f}s; transcription: {time.monotonic() - loaded:.2f}s"
            )
            return
        chat = load_chat(args.config)
        overrides = {
            key: getattr(args, key)
            for key in ("channel", "recipient", "input_device", "wow_dir")
            if getattr(args, key) is not None
        }
        chat = replace(chat, **overrides)
        chat.validate()
        if args.check:
            import os

            print(
                json.dumps(
                    {
                        "version": VERSION,
                        "system": platform.system(),
                        "python": platform.python_version(),
                        "display_available": bool(os.environ.get("DISPLAY")),
                        "speech": asdict(speech),
                        "bindings": chat.bindings,
                        "mode": chat.mode,
                        "channel": chat.channel,
                        "addon_installed": bool(
                            chat.wow_dir
                            and (
                                Path(chat.wow_dir)
                                / "Interface/AddOns/ThumbTalk/ThumbTalk.toc"
                            ).is_file()
                        ),
                    },
                    indent=2,
                )
            )
            return
        from thumbtalk_ui import run_app

        print(
            f"ThumbTalk {VERSION} · build {TEST_BUILD} · {platform.system()}",
            flush=True,
        )
        run_app(
            args.config,
            speech,
            chat,
            headless=args.headless,
            practice=args.practice,
            startup=args.startup,
        )
    except (OSError, ValueError, RuntimeError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
