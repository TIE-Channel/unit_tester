"""Layout geometry helpers used to demonstrate ut specs.

A shape is a mapping {"layer": str, "bbox": [[x1, y1], [x2, y2]]}.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def bbox_area(bbox: Any) -> float:
    """Area of a bounding box ((x1, y1), (x2, y2)), in square units."""
    (x1, y1), (x2, y2) = bbox
    return abs((x2 - x1) * (y2 - y1))


def layers_used(shapes: list[dict[str, Any]]) -> set[str]:
    """Distinct layer names used by `shapes`."""
    return {shape["layer"] for shape in shapes}


def scale_shapes(shapes: list[dict[str, Any]], factor: float) -> list[dict[str, Any]]:
    """Copies of `shapes` with every coordinate multiplied by `factor`."""
    if factor <= 0:
        raise ValueError(f"scale factor must be positive, got {factor}")
    return [{**s, "bbox": [[x * factor, y * factor] for x, y in s["bbox"]]} for s in shapes]


def write_shapes_json(shapes: list[dict[str, Any]], dest: str) -> dict[str, int]:
    """Write shapes grouped by layer to `dest` as JSON; return the number of shapes per layer."""
    by_layer: dict[str, list[Any]] = {}
    for shape in shapes:
        by_layer.setdefault(shape["layer"], []).append(shape["bbox"])
    Path(dest).write_text(json.dumps({"layers": by_layer}, indent=1), encoding="utf-8")
    return {layer: len(boxes) for layer, boxes in by_layer.items()}
