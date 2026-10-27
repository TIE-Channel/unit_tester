"""Test spec model, discovery and validation.

A spec file (`<name>.test.yaml`) describes one procedure and a list of cases.
Validation happens in two passes: the JSON Schema (structure), then semantic
checks the schema cannot express (input files exist, regexes compile, ids are
unique, language-specific rules).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Iterator

import jsonschema
import yaml
from jsonschema.exceptions import best_match

from ut.errors import SpecError
from ut.yamlio import load_yaml_text

SCHEMA_PATH = Path(__file__).with_name("schema") / "testspec.v1.json"
SPEC_SUFFIXES = (".test.yaml", ".test.yml")
TOKEN_KEYS = ("file", "dir", "out", "tmpdir", "value")
# Tags become pytest markers; these names already mean something to pytest or ut.
RESERVED_TAGS = {"python", "skill", "skip", "skipif", "xfail", "parametrize", "usefixtures",
                 "filterwarnings", "timeout", "tryfirst", "trylast"}
_SKIP_DIRS = {"__pycache__", "node_modules"}
_CASE_ID_LINE = re.compile(r"^\s*(?:-\s+)?id:\s*[\"']?([A-Za-z0-9_][A-Za-z0-9_.-]*)[\"']?\s*(?:#.*)?$")


def is_spec_file(path: Path | str) -> bool:
    return Path(path).name.endswith(SPEC_SUFFIXES)


@lru_cache(maxsize=None)
def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def _validator() -> jsonschema.Draft202012Validator:
    schema = load_schema()
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema)


@dataclass(eq=False)
class Spec:
    path: Path
    data: dict[str, Any]
    cases: list[Case] = field(default_factory=list)

    @property
    def dir(self) -> Path:
        return self.path.parent

    @property
    def name(self) -> str:
        name = self.path.name
        for suffix in SPEC_SUFFIXES:
            if name.endswith(suffix):
                return name[: -len(suffix)]
        return name

    @property
    def language(self) -> str:
        return self.data["target"]["language"]

    @property
    def procedure(self) -> str:
        return self.data["target"]["procedure"]

    @property
    def source(self) -> str | None:
        return self.data["target"].get("source")

    @property
    def description(self) -> str:
        return self.data.get("description", "")

    @property
    def tags(self) -> list[str]:
        return list(self.data.get("tags", []))


@dataclass(eq=False)
class Case:
    spec: Spec
    data: dict[str, Any]
    line: int | None = None  # 1-based line of `id:` in the spec file

    @property
    def id(self) -> str:
        return self.data["id"]

    @property
    def description(self) -> str:
        return self.data.get("description", "")

    @property
    def args(self) -> list[Any]:
        return self.data.get("args", [])

    @property
    def kwargs(self) -> dict[str, Any]:
        return self.data.get("kwargs", {})

    @property
    def expect(self) -> dict[str, Any]:
        return self.data["expect"]

    @property
    def tags(self) -> list[str]:
        merged = self.spec.tags
        merged += [t for t in self.data.get("tags", []) if t not in merged]
        return merged

    @property
    def skip(self) -> str | None:
        return self.data.get("skip") or self.spec.data.get("skip")

    @property
    def xfail(self) -> str | None:
        return self.data.get("xfail") or self.spec.data.get("xfail")

    @property
    def timeout(self) -> float | None:
        return self.data.get("timeout", self.spec.data.get("timeout"))

    @property
    def mocks(self) -> dict[str, Any]:
        return {**self.spec.data.get("mocks", {}), **self.data.get("mocks", {})}

    @property
    def stubs(self) -> dict[str, str]:
        return {**self.spec.data.get("stubs", {}), **self.data.get("stubs", {})}


def iter_tokens(case: Case) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield (location, token) for every token object in the case's arguments."""

    def walk(value: Any, loc: str) -> Iterator[tuple[str, dict[str, Any]]]:
        if isinstance(value, dict):
            yield loc, value
        elif isinstance(value, list):
            for i, item in enumerate(value):
                yield from walk(item, f"{loc}[{i}]")

    for i, arg in enumerate(case.args):
        yield from walk(arg, f"args[{i}]")
    for key, arg in case.kwargs.items():
        yield from walk(arg, f"kwargs.{key}")


def token_kind(token: dict[str, Any]) -> str:
    return next(k for k in TOKEN_KEYS if k in token)


def load_spec(path: Path | str, *, allow_missing_golden: bool = False) -> Spec:
    """Load and validate a spec, raising SpecError listing every problem."""
    spec, problems = check_spec(path, allow_missing_golden=allow_missing_golden)
    if problems:
        raise SpecError(Path(path), problems)
    assert spec is not None
    return spec


def check_spec(path: Path | str, *, allow_missing_golden: bool = False) -> tuple[Spec | None, list[str]]:
    """Load and validate a spec; return (spec, []) or (None, problems)."""
    path = Path(path).resolve()
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return None, [f"cannot read file: {exc}"]
    try:
        data = load_yaml_text(text)
    except yaml.YAMLError as exc:
        return None, [f"YAML error: {exc}"]
    if not isinstance(data, dict):
        return None, ["top level must be a mapping"]

    problems = _schema_problems(data)
    if problems:
        return None, problems

    spec = Spec(path=path, data=data)
    lines = _case_lines(text)
    spec.cases = [Case(spec, c, lines.get(c["id"])) for c in data["cases"]]
    problems = _semantic_problems(spec, allow_missing_golden)
    return (None, problems) if problems else (spec, [])


def discover(paths: Iterable[Path | str], exclude: Iterable[Path] = ()) -> list[Path]:
    """Spec files under `paths` (files or directories), sorted.

    Hidden directories, __pycache__ and anything under `exclude` are skipped.
    """
    excluded = {Path(p).resolve() for p in exclude}
    found: set[Path] = set()
    for p in paths:
        p = Path(p).resolve()
        if p.is_file():
            if is_spec_file(p):
                found.add(p)
            continue
        if not p.is_dir() or p in excluded:
            continue
        for dirpath, dirnames, filenames in os.walk(p):
            here = Path(dirpath)
            dirnames[:] = sorted(
                n for n in dirnames
                if not n.startswith(".") and n not in _SKIP_DIRS and (here / n).resolve() not in excluded)
            found.update(here / f for f in filenames if is_spec_file(f))
    return sorted(found)


def _where(path: Iterable[Any]) -> str:
    out = ""
    for part in path:
        out += f"[{part}]" if isinstance(part, int) else (f".{part}" if out else str(part))
    return out or "(top level)"


def _schema_problems(data: dict[str, Any]) -> list[str]:
    problems: list[str] = []

    def add(err: Any, message: str | None = None) -> None:
        line = f"{_where(err.absolute_path)}: {message or _message(err)}"
        if line not in problems:
            problems.append(line)

    errors = sorted(_validator().iter_errors(data), key=lambda e: [str(p) for p in e.absolute_path])
    for err in errors:
        in_args = any(p in ("args", "kwargs") for p in err.absolute_path)
        if err.validator in ("anyOf", "oneOf") and in_args and isinstance(err.instance, dict):
            # A recognisable token with a bad field (e.g. an absolute path) has
            # errors below the token itself; anything else is not a token at all.
            deeper = [e for e in _descendants(err) if len(e.absolute_path) > len(err.absolute_path)]
            for e in deeper:
                add(e)
            if not deeper:
                add(err, "an argument mapping must be exactly one token: {file: ...}, {dir: ...}, "
                         "{out: ...}, {tmpdir: ...} (each optionally with 'as'), or {value: ...} "
                         f"for a literal mapping; got keys {sorted(err.instance)}")
        elif err.context:
            add(best_match(err.context))
        else:
            add(err)
    return problems


def _descendants(err: Any) -> Iterator[Any]:
    for child in err.context or []:
        yield child
        yield from _descendants(child)


def _message(err: Any) -> str:
    """jsonschema's message, reworded where it is cryptic for spec authors."""
    if err.validator == "pattern" and err.validator_value == load_schema()["$defs"]["relpath"]["pattern"]:
        return f"{err.instance!r} must be a relative path with forward slashes"
    if err.validator == "additionalProperties" and isinstance(err.instance, dict):
        known = err.schema.get("properties", {})
        patterns = err.schema.get("patternProperties", {})
        unknown = [k for k in err.instance if k not in known and not any(re.search(p, k) for p in patterns)]
        if unknown:
            hint = f" (allowed: {', '.join(known)})" if known else ""
            return f"unknown key{'s' if len(unknown) > 1 else ''} {', '.join(map(repr, unknown))}{hint}"
    return err.message


def _case_lines(text: str) -> dict[str, int]:
    lines: dict[str, int] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        m = _CASE_ID_LINE.match(line)
        if m:
            lines.setdefault(m.group(1), number)
    return lines


def _semantic_problems(spec: Spec, allow_missing_golden: bool) -> list[str]:
    from ut.adapters import adapter_class

    problems: list[str] = []

    def check_regexes(where: str, patterns: Iterable[str]) -> None:
        for pattern in patterns:
            try:
                re.compile(pattern)
            except re.error as exc:
                problems.append(f"{where}: invalid regex {pattern!r}: {exc}")

    def check_golden(where: str, rel: str | None) -> None:
        if rel and not allow_missing_golden and not (spec.dir / rel).is_file():
            problems.append(f"{where}: golden file not found: {rel} (create it with `ut bless`)")

    for tag in spec.tags:
        if tag in RESERVED_TAGS:
            problems.append(f"tags: '{tag}' is reserved")

    seen_ids: set[str] = set()
    for i, case in enumerate(spec.cases):
        where = f"cases[{i}] ({case.id})"
        if case.id in seen_ids:
            problems.append(f"{where}: duplicate case id")
        seen_ids.add(case.id)
        for tag in case.data.get("tags", []):
            if tag in RESERVED_TAGS:
                problems.append(f"{where}.tags: '{tag}' is reserved")

        inputs: dict[str, str] = {}
        outputs: list[tuple[str, str]] = []
        for loc, token in iter_tokens(case):
            kind = token_kind(token)
            if kind == "value":
                continue
            rel = token[kind]
            if kind in ("file", "dir"):
                src = spec.dir / rel
                if kind == "file" and not src.is_file():
                    problems.append(f"{where}.{loc}: input file not found: {rel}")
                elif kind == "dir" and not src.is_dir():
                    problems.append(f"{where}.{loc}: input directory not found: {rel}")
                name = Path(rel).name
                if inputs.get(name, rel) != rel:
                    problems.append(f"{where}.{loc}: inputs {inputs[name]} and {rel} would both be copied to "
                                    f"'{name}' in the sandbox")
                inputs[name] = rel
            else:
                if ".." in Path(rel).parts:
                    problems.append(f"{where}.{loc}: '{rel}' must stay inside the sandbox")
                outputs.append((loc, Path(rel).parts[0]))
        for loc, first in outputs:
            if first in inputs:
                problems.append(f"{where}.{loc}: '{first}' collides with input {inputs[first]} in the sandbox")

        expect = case.expect
        if "returns" in expect and "error" in expect:
            problems.append(f"{where}.expect: 'returns' and 'error' are mutually exclusive")
        returns = expect.get("returns", {})
        error = expect.get("error", {})
        if "regex" in returns:
            check_regexes(f"{where}.expect.returns.regex", [returns["regex"]])
        if "match" in error:
            check_regexes(f"{where}.expect.error.match", [error["match"]])
        if "schema" in returns:
            try:
                jsonschema.Draft202012Validator.check_schema(returns["schema"])
            except jsonschema.SchemaError as exc:
                problems.append(f"{where}.expect.returns.schema: invalid JSON Schema: {exc.message}")
        check_golden(f"{where}.expect.returns.golden", returns.get("golden"))
        for stream in ("stdout", "stderr"):
            text = expect.get(stream)
            if isinstance(text, dict):
                check_regexes(f"{where}.expect.{stream}", text.get("matches", []) + text.get("ignore", []))
                check_golden(f"{where}.expect.{stream}.golden", text.get("golden"))
        for j, fexp in enumerate(expect.get("files", [])):
            fwhere = f"{where}.expect.files[{j}] ({fexp['path']})"
            if ".." in Path(fexp["path"]).parts:
                problems.append(f"{fwhere}: path must stay inside the sandbox")
            mode = fexp.get("compare", "text")
            if mode != "text":
                for key in ("ignore", "contains", "not_contains", "matches"):
                    if key in fexp:
                        problems.append(f"{fwhere}: '{key}' only applies to compare: text")
            check_regexes(fwhere, fexp.get("ignore", []) + fexp.get("matches", []))
            check_golden(fwhere, fexp.get("golden"))
        for rel in expect.get("files_absent", []):
            if ".." in Path(rel).parts:
                problems.append(f"{where}.expect.files_absent: '{rel}' must stay inside the sandbox")

    problems += adapter_class(spec.language).validate_spec(spec)
    return problems
