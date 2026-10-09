"""Route Qt overlays to Gamescope's root X server, keeping game input separate."""

import os
import platform
from pathlib import Path


def overlay_display():
    game = os.environ.get("DISPLAY")
    if platform.system() != "Linux" or not game:
        return None
    from Xlib.display import Display
    from Xlib import Xatom

    def server_id(name):
        display = None
        try:
            display = Display(name)
            atom = display.intern_atom(
                "GAMESCOPE_XWAYLAND_SERVER_ID", only_if_exists=True
            )
            prop = (
                display.screen().root.get_full_property(atom, Xatom.CARDINAL)
                if atom
                else None
            )
            return int(prop.value[0]) if prop is not None and len(prop.value) else None
        except Exception:
            return None
        finally:
            if display is not None:
                display.close()

    identity = server_id(game)
    if identity is None or identity == 0:
        return game
    # Verify the server's Gamescope identity; never assume that :0 is the root.
    sockets = sorted(Path("/tmp/.X11-unix").glob("X*"))
    for socket in sockets[:32]:
        number = socket.name[1:]
        if number.isdigit():
            candidate = ":" + number
            if server_id(candidate) == 0:
                return candidate
    print(
        "Overlay: Gamescope root display was not found; using game display.", flush=True
    )
    return game
