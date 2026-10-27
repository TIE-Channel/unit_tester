"""Text utilities used to demonstrate ut specs."""

from __future__ import annotations

import getpass
import time
from pathlib import Path


def word_count(path: str) -> dict[str, int]:
    """Count lines, words and characters in a UTF-8 text file."""
    text = Path(path).read_text(encoding="utf-8")
    return {"lines": len(text.splitlines()), "words": len(text.split()), "chars": len(text)}


def normalize_cell_name(name: str) -> str:
    """Canonical cell name: trimmed, lower case, inner whitespace runs become one underscore."""
    if not isinstance(name, str):
        raise TypeError(f"cell name must be a string, not {type(name).__name__}")
    cleaned = "_".join(name.strip().lower().split())
    if not cleaned:
        raise ValueError("cell name is empty")
    return cleaned


def write_report(src: str, dest: str, title: str = "Word report") -> int:
    """Write a word-frequency report for `src` to `dest`; return the number of distinct words."""
    words = Path(src).read_text(encoding="utf-8").lower().split()
    counts: dict[str, int] = {}
    for word in words:
        counts[word] = counts.get(word, 0) + 1
    lines = [
        f"# {title}",
        f"# Generated on {time.strftime('%Y-%m-%d %H:%M:%S')} by {getpass.getuser()}",
        f"# Source: {src}",
        "",
    ]
    lines += [f"{word:<12} {n}" for word, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    Path(dest).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {len(counts)} words to {dest}")
    return len(counts)
