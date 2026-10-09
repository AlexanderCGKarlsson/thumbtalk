"""The shared app mark and Windows taskbar identity."""

import sys
from thumbtalk_setup import resources


def apply_branding(app):
    from PySide6.QtGui import QIcon

    folder = resources() / (
        "assets" if getattr(sys, "frozen", False) else "packaging/assets"
    )
    app.setApplicationName("ThumbTalk")
    app.setWindowIcon(QIcon(str(folder / "thumbtalk.ico")))
    if sys.platform == "win32":
        import ctypes

        shell = ctypes.WinDLL("shell32", use_last_error=True)
        identify = shell.SetCurrentProcessExplicitAppUserModelID
        identify.argtypes = [ctypes.c_wchar_p]
        identify.restype = ctypes.c_long
        if identify("ThumbTalk.Companion") < 0:
            print("Windows could not set ThumbTalk's taskbar identity.", flush=True)
