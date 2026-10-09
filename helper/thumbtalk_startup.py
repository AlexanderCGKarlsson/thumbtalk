"""Opt-in Windows sign-in startup, scoped to the current user."""

from pathlib import Path
import subprocess
import sys

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "ThumbTalk"


def startup_enabled():
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, kind = winreg.QueryValueEx(key, VALUE_NAME)
            return kind == winreg.REG_SZ and bool(value)
    except FileNotFoundError:
        return False


def startup_command(config):
    if not getattr(sys, "frozen", False):
        raise RuntimeError("Install the Windows app to enable Start with Windows.")
    command = subprocess.list2cmdline(
        [sys.executable, "--startup", "--config", str(Path(config).resolve())]
    )
    if len(command) > 260:
        raise RuntimeError(
            "The installation or settings path is too long for Windows startup."
        )
    return command


def set_startup(enabled, config):
    if sys.platform != "win32":
        raise RuntimeError("Start with Windows is only available on Windows.")
    import winreg

    if enabled:
        command = startup_command(config)
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command)
    else:
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.DeleteValue(key, VALUE_NAME)
        except FileNotFoundError:
            pass
