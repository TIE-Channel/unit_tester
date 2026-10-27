"""Canonical, JSON-compatible form of values, used for every comparison.

Adapters convert what a procedure returns into this form, so the same checks
work for every language: tuples become lists, sets become sorted lists, paths
become strings, and anything without a JSON equivalent becomes a tagged
object such as {"$bytes": "..."} or {"$repr": "..."}.
"""

from __future__ import annotations

import dataclasses
import datetime
import enum
import json
import math
import os
import re
from pathlib import Path, PurePath
from typing import Any

SANDBOX_MARK = "<sandbox>"


class Normalizer:
    """Replaces the sandbox location in text with `<sandbox>`, so results and
    goldens do not depend on where the sandbox happens to be.

    The rest of a path inside the sandbox is written with forward slashes, so
    `C:\\...\\sandbox\\x\\out.txt` and `/home/.../sandbox/x/out.txt` both become
    `<sandbox>/x/out.txt` and goldens match on Windows and Linux.
    """

    def __init__(self, sandbox: Path | None = None):
        forms: set[str] = set()
        if sandbox is not None:
            for p in (Path(sandbox), Path(sandbox).resolve()):
                forms.update((str(p), p.as_posix()))
        self._pattern = None
        if forms:
            alternatives = "|".join(re.escape(f) for f in sorted(forms, key=len, reverse=True))
            flags = re.IGNORECASE if os.name == "nt" else 0
            # (?![\w.-]) stops `.../case1` from matching inside `.../case10`
            self._pattern = re.compile(rf"(?:{alternatives})(?![\w.-])((?:[\\/][^\s\"'<>|*?:]*)?)", flags)

    def text(self, s: str) -> str:
        if self._pattern is None:
            return s
        return self._pattern.sub(lambda m: SANDBOX_MARK + m.group(1).replace("\\", "/"), s)


def canonicalize(value: Any, normalizer: Normalizer | None = None) -> Any:
    n = normalizer or Normalizer()
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, enum.Enum):
        return canonicalize(value.value, n)
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        if math.isnan(value):
            return {"$float": "nan"}
        if math.isinf(value):
            return {"$float": "inf" if value > 0 else "-inf"}
        return value
    if isinstance(value, str):
        return n.text(value)
    if isinstance(value, PurePath):
        return n.text(value.as_posix())
    if isinstance(value, (bytes, bytearray)):
        return {"$bytes": bytes(value).hex()}
    if isinstance(value, dict):
        return {_key(k, n): canonicalize(v, n) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [canonicalize(v, n) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted((canonicalize(v, n) for v in value), key=sort_key)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return canonicalize(dataclasses.asdict(value), n)
    if isinstance(value, (datetime.date, datetime.time)):
        return value.isoformat()
    tolist = getattr(value, "tolist", None)  # numpy arrays and scalars
    if callable(tolist):
        return canonicalize(tolist(), n)
    return {"$repr": n.text(repr(value))}


def _key(key: Any, n: Normalizer) -> str:
    if isinstance(key, str):
        return n.text(key)
    c = canonicalize(key, n)
    return c if isinstance(c, str) else json.dumps(c, sort_keys=True)


def sort_key(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def dumps(value: Any, width: int = 88) -> str:
    """Indented JSON that keeps any array or object fitting in `width` on one
    line, so goldens and diffs stay short and readable."""

    def fmt(v: Any, level: int) -> str:
        compact = json.dumps(v, sort_keys=True, ensure_ascii=False)
        if not isinstance(v, (list, dict)) or not v or len(compact) + 2 * level <= width:
            return compact
        pad, end = "  " * (level + 1), "  " * level
        if isinstance(v, list):
            return "[\n" + ",\n".join(pad + fmt(x, level + 1) for x in v) + f"\n{end}]"
        items = (f"{pad}{json.dumps(k, ensure_ascii=False)}: {fmt(v[k], level + 1)}" for k in sorted(v))
        return "{\n" + ",\n".join(items) + f"\n{end}}}"

    return fmt(value, 0)
