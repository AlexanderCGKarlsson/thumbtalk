"""One-way Linux paste into the addon's local review receiver.

No Ctrl+C, clipboard replies, native chat commands or Enter. The addon displays
the complete packet in chat history and releases its own input focus. Actual
sending happens only after the user accepts, through the ordinary sender.
"""

import re
import secrets
import time
import zlib


def packet(text, identifier):
    data = text.encode("utf-8")
    if (
        not text.startswith("/")
        or len(data) > 255
        or any(ord(c) < 32 or ord(c) == 127 for c in text)
        or re.fullmatch("[0-9a-f]{8}", identifier) is None
    ):
        raise ValueError("Invalid private review packet.")
    return f"TTREVIEW2:{identifier}:{data.hex()}:{zlib.adler32(data):08x};"


def transfer(text, keyboard, clipboard, guard, log, sleep=time.sleep):
    from pynput.keyboard import Key
    from thumbtalk_platform import DeliveryStopped

    def check():
        if not guard():
            raise DeliveryStopped(
                "Review interrupted by controls or focus. Nothing sent."
            )

    def signal(number):
        code = str(58 + number)
        keyboard.keys(
            ("29:1", "42:1", code + ":1", code + ":0", "42:0", "29:0"),
            (Key.ctrl_l, Key.shift_l, getattr(Key, f"f{number}")),
            guard,
        )

    wire = packet(text, secrets.token_hex(4))
    check()
    keyboard.start()
    lease, opened, completed = None, False, False
    try:
        # Publish before taking any input focus; clipboard startup can be slow.
        lease = clipboard.acquire_review(wire)
        check()
        clipboard.arm(lease)
        log("Review: opening local receiver; one-way paste, no clipboard reply")
        opened = True
        signal(8)
        sleep(0.25)  # Allow the receiver's focus/layout to update before Ctrl+V.
        check()
        clipboard.arm(lease)
        keyboard.keys(("29:1", "47:1", "47:0", "29:0"), (Key.ctrl_l, "v"), guard)
        clipboard.wait_for_paste(lease)  # Same 300 ms grace as ordinary sending.
        check()
        completed = True
        log("Review: paste requested once; check WoW chat; nothing sent")
    finally:
        if opened and not completed and guard():
            try:
                signal(6)
            except Exception:
                log("Review: cancel key unavailable; receiver will time out")
        if lease is not None:
            clipboard.restore(lease)
