"""Helpers for the engine's own tests.

End-to-end tests run `python -m ut` in a subprocess against small repositories
written into tmp_path, so they exercise the real CLI, plugin loading and exit
codes without nesting pytest sessions.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

import pytest

ENGINE_ROOT = Path(__file__).resolve().parents[1]
DEMO_REPO = ENGINE_ROOT / "examples" / "demo_repo"


@dataclass
class Result:
    code: int
    out: str


def run_ut(cwd: Path, *args: str) -> Result:
    proc = subprocess.run([sys.executable, "-m", "ut", *args], cwd=cwd, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=300)
    return Result(proc.returncode, proc.stdout + proc.stderr)


def write_files(root: Path, files: dict[str, str]) -> Path:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(content).lstrip("\n"), encoding="utf-8")
    return root


@pytest.fixture
def demo_repo(tmp_path: Path) -> Path:
    dest = tmp_path / "demo_repo"
    shutil.copytree(DEMO_REPO, dest, ignore=shutil.ignore_patterns("ut-out", "__pycache__", ".pytest_cache"))
    return dest


@pytest.fixture
def repo(tmp_path: Path):
    """A minimal repository: ut.toml with src/ on the Python path, plus the given files."""

    def make(files: dict[str, str]) -> Path:
        root = tmp_path / "repo"
        base = {
            "ut.toml": """
                [python]
                paths = ["src"]
            """,
            "pytest.ini": "[pytest]\n",
        }
        return write_files(root, {**base, **files})

    return make
