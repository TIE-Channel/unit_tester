"""`ut new`: create a spec skeleton at the conventional location.

The location comes from `layout.spec_path` in ut.toml, so people and
Claude skills put specs in the same place.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from ut.adapters.python import PROCEDURE_PATTERN as PYTHON_PROCEDURE
from ut.adapters.skill import PROCEDURE_PATTERN as SKILL_PROCEDURE
from ut.config import Config

_AREA = re.compile(r"^[A-Za-z0-9_][\w.-]*(/[A-Za-z0-9_][\w.-]*)*$")

TEMPLATE = """\
schema_version: 1
target:
  language: {language}
  procedure: {procedure}
{source_line}description: TODO what {name} does
tags: []
cases:
  # One case per behaviour: typical input, boundaries, invalid input, error
  # paths, files written. Remove `skip` once a case is filled in.
  - id: todo
    description: TODO
    skip: "scaffolded by `ut new`, not written yet"
    args: []
    expect:
      returns: {{value: null}}
"""


def spec_location(cfg: Config, language: str, procedure: str, area: str | None = None) -> Path:
    if language == "python":
        if not PYTHON_PROCEDURE.match(procedure):
            raise ValueError(f"expected 'package.module:function', got {procedure!r}")
        module, attr = procedure.split(":")
        name = attr.replace(".", "_")
        area = area or module.replace(".", "/")
    else:
        if not SKILL_PROCEDURE.match(procedure):
            raise ValueError(f"{procedure!r} is not a SKILL procedure name")
        name = procedure
        area = area or "misc"
    area = area.strip("/")
    if not _AREA.match(area) or ".." in area.split("/"):
        raise ValueError(f"invalid area {area!r}; use a relative path like 'pins' or 'reports/lef'")
    tests_root = cfg.tests[0] if cfg.tests else cfg.root / "tests"
    tests_rel = Path(os.path.relpath(tests_root, cfg.root)).as_posix()
    return cfg.root / cfg.spec_path.format(tests=tests_rel, language=language, area=area, name=name)


def find_python_source(cfg: Config, module: str) -> str | None:
    """The module's file under the configured paths, found without importing it."""
    rel = module.replace(".", "/")
    for base in [*cfg.python_paths, cfg.root]:
        for candidate in (base / f"{rel}.py", base / rel / "__init__.py"):
            if candidate.is_file():
                return cfg.rel(candidate)
    return None


def scaffold(cfg: Config, language: str, procedure: str, *, area: str | None = None,
             source: str | None = None, force: bool = False) -> Path:
    path = spec_location(cfg, language, procedure, area)
    if path.exists() and not force:
        raise FileExistsError(f"{cfg.rel(path)} already exists (use --force to overwrite)")
    if language == "python" and source is None:
        source = find_python_source(cfg, procedure.split(":")[0])
    path.parent.mkdir(parents=True, exist_ok=True)
    for sub in ("data", "golden"):
        (path.parent / sub).mkdir(exist_ok=True)
        (path.parent / sub / ".gitkeep").touch()
    path.write_text(TEMPLATE.format(
        language=language,
        procedure=json.dumps(procedure),
        source_line=f"  source: {json.dumps(source)}\n" if source else "",
        name=path.parent.name,
    ), encoding="utf-8")
    return path
