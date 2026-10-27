"""Per-repository configuration, read from ut.toml.

The file is optional: without one, specs are looked up under ./tests and
sandboxes go to ./ut-out. Unknown keys are rejected so typos fail loudly.
"""

from __future__ import annotations

import string
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

from ut.errors import ConfigError

CONFIG_NAME = "ut.toml"
LANGUAGES = ("python", "skill")
KEEP_POLICIES = ("failed", "always", "never")
DEFAULT_SPEC_PATH = "{tests}/{language}/{area}/{name}/{name}.test.yaml"
_SPEC_PATH_FIELDS = {"tests", "language", "area", "name"}


@dataclass
class Suite:
    name: str
    markers: str = ""
    languages: list[str] = field(default_factory=list)
    paths: list[Path] = field(default_factory=list)


@dataclass
class Config:
    root: Path
    file: Path | None = None
    tests: list[Path] = field(default_factory=list)
    out_dir: Path = Path("ut-out")
    keep_sandbox: str = "failed"
    python_paths: list[Path] = field(default_factory=list)
    suites: dict[str, Suite] = field(default_factory=dict)
    spec_path: str = DEFAULT_SPEC_PATH

    @classmethod
    def default(cls, root: Path) -> Config:
        root = root.resolve()
        return cls(root=root, tests=[root / "tests"], out_dir=root / "ut-out")

    @property
    def sandbox_root(self) -> Path:
        return self.out_dir / "sandbox"

    def rel(self, path: Path) -> str:
        """`path` relative to the repository root when inside it, for display."""
        try:
            return Path(path).resolve().relative_to(self.root).as_posix()
        except ValueError:
            return str(path)


def find_config(start: Path) -> Path | None:
    start = Path(start).resolve()
    for directory in (start, *start.parents):
        candidate = directory / CONFIG_NAME
        if candidate.is_file():
            return candidate
    return None


def load_config(path: Path | str | None = None, start: Path | None = None) -> Config:
    """Load `path`, or the nearest ut.toml at or above `start` (default: cwd)."""
    if path is None:
        found = find_config(start or Path.cwd())
        if found is None:
            return Config.default(start or Path.cwd())
        path = found
    path = Path(path).resolve()
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from None
    return _parse(data, path)


def _parse(data: dict[str, Any], path: Path) -> Config:
    root = path.parent
    cfg = Config.default(root)
    cfg.file = path

    def fail(msg: str) -> ConfigError:
        return ConfigError(f"{path}: {msg}")

    def table(name: str, allowed: set[str]) -> dict[str, Any]:
        value = data.get(name, {})
        if not isinstance(value, dict):
            raise fail(f"[{name}] must be a table")
        unknown = set(value) - allowed
        if unknown:
            raise fail(f"unknown key(s) in [{name}]: {', '.join(sorted(unknown))}")
        return value

    def str_list(value: Any, where: str) -> list[str]:
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise fail(f"{where} must be a list of strings")
        return value

    unknown = set(data) - {"ut", "python", "suites", "layout"}
    if unknown:
        raise fail(f"unknown table(s): {', '.join(sorted(unknown))}")

    ut = table("ut", {"tests", "out_dir", "keep_sandbox"})
    if "tests" in ut:
        cfg.tests = [root / p for p in str_list(ut["tests"], "ut.tests")]
    if "out_dir" in ut:
        if not isinstance(ut["out_dir"], str):
            raise fail("ut.out_dir must be a string")
        cfg.out_dir = root / ut["out_dir"]
    if "keep_sandbox" in ut:
        if ut["keep_sandbox"] not in KEEP_POLICIES:
            raise fail(f"ut.keep_sandbox must be one of {', '.join(KEEP_POLICIES)}")
        cfg.keep_sandbox = ut["keep_sandbox"]

    python = table("python", {"paths"})
    cfg.python_paths = [root / p for p in str_list(python.get("paths", []), "python.paths")]

    layout = table("layout", {"spec_path"})
    if "spec_path" in layout:
        template = layout["spec_path"]
        fields = {f for _, f, _, _ in string.Formatter().parse(template) if f}
        if not isinstance(template, str) or "name" not in fields or fields - _SPEC_PATH_FIELDS:
            raise fail("layout.spec_path must use only {tests} {language} {area} {name} and include {name}")
        if not template.endswith((".test.yaml", ".test.yml")):
            raise fail("layout.spec_path must end with .test.yaml")
        cfg.spec_path = template

    suites = data.get("suites", {})
    if not isinstance(suites, dict):
        raise fail("[suites] must be a table")
    for name, body in suites.items():
        if not isinstance(body, dict):
            raise fail(f"[suites.{name}] must be a table")
        unknown = set(body) - {"markers", "languages", "paths"}
        if unknown:
            raise fail(f"unknown key(s) in [suites.{name}]: {', '.join(sorted(unknown))}")
        markers = body.get("markers", "")
        if not isinstance(markers, str):
            raise fail(f"suites.{name}.markers must be a string")
        languages = str_list(body.get("languages", []), f"suites.{name}.languages")
        bad = set(languages) - set(LANGUAGES)
        if bad:
            raise fail(f"suites.{name}.languages: unknown language(s) {', '.join(sorted(bad))}")
        paths = [root / p for p in str_list(body.get("paths", []), f"suites.{name}.paths")]
        cfg.suites[name] = Suite(name=name, markers=markers, languages=languages, paths=paths)

    return cfg
