"""Per-case sandbox directories and argument token resolution.

Every case runs in its own empty directory under <out_dir>/sandbox. Input
fixtures are copied in, outputs are written there, and the procedure runs with
the sandbox as its working directory, so nothing touches the repository.
"""

from __future__ import annotations

import copy
import hashlib
import os
import shutil
from pathlib import Path
from typing import Any

from ut.config import Config
from ut.spec import Case, token_kind

# Written into the sandbox root; only a directory carrying it is ever wiped,
# so a misconfigured out_dir cannot delete unrelated files.
ROOT_MARKER = ".ut-sandbox-root"


def reset_root(root: Path) -> None:
    """Empty the sandbox root at the start of a run, so it reflects only this run."""
    if (root / ROOT_MARKER).is_file():
        shutil.rmtree(root, ignore_errors=True)


def ensure_root(root: Path) -> None:
    if not root.exists():
        root.mkdir(parents=True, exist_ok=True)
        (root / ROOT_MARKER).touch()


def prune_empty_dirs(root: Path) -> None:
    """Remove directories left empty after passing cases' sandboxes were deleted."""
    if not root.is_dir():
        return
    # bottom-up, checking the disk rather than os.walk's listing, which predates
    # the removal of the children
    for dirpath, _, _ in os.walk(root, topdown=False):
        path = Path(dirpath)
        if path != root and not any(path.iterdir()):
            try:
                path.rmdir()
            except OSError:
                pass


def sandbox_path(config: Config, case: Case) -> Path:
    spec_path = case.spec.path
    try:
        rel = spec_path.relative_to(config.root)
        parts = [*rel.parent.parts, case.spec.name]
    except ValueError:
        digest = hashlib.sha1(str(spec_path).encode()).hexdigest()[:8]
        parts = ["_external", f"{case.spec.name}-{digest}"]
    return config.sandbox_root.joinpath(*parts, case.id)


def prepare(path: Path) -> Path:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def remove(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def resolve_arguments(case: Case, sandbox: Path) -> tuple[list[Any], dict[str, Any]]:
    """Turn the case's args/kwargs into call arguments, materialising tokens in the sandbox."""

    def resolve(value: Any) -> Any:
        if isinstance(value, list):
            return [resolve(v) for v in value]
        if not isinstance(value, dict):
            return value
        kind = token_kind(value)
        if kind == "value":
            return copy.deepcopy(value["value"])
        rel = value[kind]
        if kind == "file":
            dest = sandbox / Path(rel).name
            if not dest.exists():
                shutil.copy2(case.spec.dir / rel, dest)
        elif kind == "dir":
            dest = sandbox / Path(rel).name
            if not dest.exists():
                shutil.copytree(case.spec.dir / rel, dest)
        elif kind == "out":
            dest = sandbox / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
        else:  # tmpdir
            dest = sandbox / rel
            dest.mkdir(parents=True, exist_ok=True)
        return dest if value.get("as") == "path" else str(dest)

    return [resolve(a) for a in case.args], {k: resolve(v) for k, v in case.kwargs.items()}
