"""Status display preferences, independent of the radial and speech engine."""

STATUS_MODES = {
    "automatic": "Only when needed",
    "always": "Always show",
    "off": "Hidden",
}
STATUS_POSITIONS = {
    "top-left": "Top left",
    "top-center": "Top centre",
    "top-right": "Top right",
    "bottom-left": "Bottom left",
    "bottom-center": "Bottom centre",
    "bottom-right": "Bottom right",
}


def status_point(position, width, height, box_width, box_height):
    vertical, horizontal = position.split("-")
    gap_x, gap_y = max(0, width - box_width), max(0, height - box_height)
    x = {"left": min(24, gap_x), "center": gap_x // 2, "right": max(0, gap_x - 24)}[
        horizontal
    ]
    y = min(24, gap_y) if vertical == "top" else max(0, gap_y - 24)
    return x, y


def idle_status(text):
    return text.startswith(("Ready", "Menu closed", "Sent"))
