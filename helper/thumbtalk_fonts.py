"""Keep bundled Linux Fontconfig paired with a compatible configuration."""

import os
from pathlib import Path
import sys


def configure_bundled_fonts():
    """Select app-local rules before any GUI or font library initializes.

    Newer distributions can ship rules that an older bundled Fontconfig cannot
    parse. Use their installed font files with our compatible rules. This changes
    only this process, never /etc/fonts or the environment of the launching game.
    Source runs, Windows and macOS retain their native font configuration.
    """
    if sys.platform != "linux" or not getattr(sys, "frozen", False):
        return
    folder = Path(sys._MEIPASS) / "fontconfig"
    config = folder / "fonts.conf"
    if not config.is_file():
        raise RuntimeError(
            "ThumbTalk's font configuration is missing. Reinstall the app."
        )
    os.environ["FONTCONFIG_FILE"] = str(config)
    os.environ["FONTCONFIG_PATH"] = str(folder)
    # A Steam runtime sysroot would redirect the font directories in our file.
    os.environ.pop("FONTCONFIG_SYSROOT", None)
