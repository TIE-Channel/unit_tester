"""Custom checks, referenced from specs as `expect.custom: demo_checks:<function>`.

A check receives the call outcome, the case and the sandbox directory. It
fails the case by raising AssertionError or returning False or a message.
"""

import json


def every_layer_has_boxes(outcome, case, sandbox):
    data = json.loads((sandbox / "shapes.json").read_text(encoding="utf-8"))
    empty = [layer for layer, boxes in data["layers"].items() if not boxes]
    assert not empty, f"layers without boxes: {empty}"
