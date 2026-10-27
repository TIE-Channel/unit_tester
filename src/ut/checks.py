"""Checks an Outcome against a case's `expect` block, and writes goldens when blessing."""

from __future__ import annotations

import difflib
import hashlib
import importlib
import json
import math
import re
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema

from ut.adapters.base import Outcome
from ut.canonical import Normalizer, canonicalize, dumps
from ut.spec import Case

MAX_DIFF_LINES = 80
IGNORED_MARK = "<ignored>"


@dataclass
class Failure:
    where: str  # e.g. "returns.value", "files[report.txt].golden"
    message: str


@dataclass
class CheckReport:
    failures: list[Failure] = field(default_factory=list)
    blessed: list[Path] = field(default_factory=list)  # golden files written or changed

    def fail(self, where: str, message: str) -> None:
        self.failures.append(Failure(where, message))


def check_outcome(case: Case, outcome: Outcome, sandbox: Path, *, bless: bool = False) -> CheckReport:
    report = CheckReport()
    expect = case.expect
    if not _check_error(expect, outcome, report):
        return report  # an unexpected error makes every other check meaningless
    norm = Normalizer(sandbox)
    if outcome.error is None and "returns" in expect:
        _check_returns(case, expect["returns"], outcome.returned, report, bless)
    for stream in ("stdout", "stderr"):
        if stream in expect:
            text = norm.text(getattr(outcome, stream)).replace("\r\n", "\n")
            _check_text(case, stream, expect[stream], text, report, bless)
    for fexp in expect.get("files", []):
        _check_file(case, fexp, sandbox, norm, report, bless)
    for rel in expect.get("files_absent", []):
        if (sandbox / rel).exists():
            report.fail(f"files_absent[{rel}]", "exists, but the case expects it not to")
    if "custom" in expect:
        _check_custom(case, expect["custom"], outcome, sandbox, report)
    return report


# --- comparison primitives -------------------------------------------------

def deep_equal(expected: Any, actual: Any) -> bool:
    """Structural equality on canonical values; numbers compare by value, but booleans are not numbers."""
    if isinstance(expected, bool) or isinstance(actual, bool):
        return isinstance(expected, bool) and isinstance(actual, bool) and expected == actual
    if _is_number(expected) and _is_number(actual):
        return expected == actual
    if isinstance(expected, list) and isinstance(actual, list):
        return len(expected) == len(actual) and all(deep_equal(e, a) for e, a in zip(expected, actual))
    if isinstance(expected, dict) and isinstance(actual, dict):
        return expected.keys() == actual.keys() and all(deep_equal(v, actual[k]) for k, v in expected.items())
    return type(expected) is type(actual) and expected == actual


def approx_equal(expected: Any, actual: Any, rel: float, abs_: float) -> bool:
    if _is_number(expected) and _is_number(actual):
        return math.isclose(expected, actual, rel_tol=rel, abs_tol=abs_)
    if isinstance(expected, list) and isinstance(actual, list):
        return len(expected) == len(actual) and all(approx_equal(e, a, rel, abs_) for e, a in zip(expected, actual))
    if isinstance(expected, dict) and isinstance(actual, dict):
        return expected.keys() == actual.keys() and all(approx_equal(v, actual[k], rel, abs_) for k, v in expected.items())
    return deep_equal(expected, actual)


def is_subset(expected: Any, actual: Any) -> bool:
    """Mappings: every expected key matches recursively. Lists: every expected
    element matches some actual element. Scalars: equal."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and is_subset(v, actual[k]) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and all(any(is_subset(e, a) for a in actual) for e in expected)
    return deep_equal(expected, actual)


def multiset_diff(expected: list[Any], actual: list[Any]) -> tuple[list[Any], list[Any]]:
    """(missing, unexpected) when comparing two lists regardless of order."""
    remaining = list(actual)
    missing = []
    for e in expected:
        index = next((i for i, a in enumerate(remaining) if deep_equal(e, a)), None)
        if index is None:
            missing.append(e)
        else:
            remaining.pop(index)
    return missing, remaining


def json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


# --- individual expectations -----------------------------------------------

def _check_error(expect: dict[str, Any], outcome: Outcome, report: CheckReport) -> bool:
    """Returns False when the call raised although no error was expected."""
    exp = expect.get("error")
    err = outcome.error
    if exp is None:
        if err is not None:
            report.fail("error", f"unexpected {err.type}: {err.message}\n\n{err.traceback.rstrip()}")
            return False
        return True
    if err is None:
        wanted = exp.get("type", "an error")
        if "match" in exp:
            wanted += f" matching /{exp['match']}/"
        report.fail("error", f"expected {wanted}, but the call returned {_compact(outcome.returned)}")
        return True
    if "type" in exp and exp["type"] not in err.types:
        report.fail("error.type", f"expected {exp['type']}, got {err.type}: {err.message}")
    if "match" in exp and not re.search(exp["match"], err.message):
        report.fail("error.match", f"message {err.message!r} does not match /{exp['match']}/")
    return True


def _check_returns(case: Case, rexp: dict[str, Any], actual: Any, report: CheckReport, bless: bool) -> None:
    for mode, expected in rexp.items():
        where = f"returns.{mode}"
        if mode == "value":
            e = canonicalize(expected)
            if not deep_equal(e, actual):
                report.fail(where, _mismatch(e, actual))
        elif mode == "approx":
            e = canonicalize(expected["value"])
            rel, abs_ = expected.get("rel", 1e-6), expected.get("abs", 1e-12)
            if not approx_equal(e, actual, rel, abs_):
                report.fail(where, f"not equal within rel={rel}, abs={abs_}\n{_mismatch(e, actual)}")
        elif mode == "regex":
            if not isinstance(actual, str):
                report.fail(where, f"expected a string, got {json_type(actual)} {_compact(actual)}")
            elif not re.search(expected, actual):
                report.fail(where, f"{actual!r} does not match /{expected}/")
        elif mode == "unordered":
            e = canonicalize(expected)
            if not isinstance(actual, list):
                report.fail(where, f"expected an array, got {json_type(actual)} {_compact(actual)}")
                continue
            missing, unexpected = multiset_diff(e, actual)
            if missing or unexpected:
                report.fail(where, f"missing:    {_compact(missing)}\nunexpected: {_compact(unexpected)}")
        elif mode == "subset":
            e = canonicalize(expected)
            if not is_subset(e, actual):
                report.fail(where, f"expected to contain: {_compact(e)}\nactual: {_short(_compact(actual), 400)}")
        elif mode == "type":
            t = json_type(actual)
            if not (t == expected or (expected == "number" and t == "integer")):
                report.fail(where, f"expected {expected}, got {t} {_compact(actual)}")
        elif mode == "length":
            if not isinstance(actual, (list, str, dict)):
                report.fail(where, f"{json_type(actual)} has no length")
            elif len(actual) != expected:
                report.fail(where, f"expected length {expected}, got {len(actual)}")
        elif mode == "schema":
            errors = list(jsonschema.Draft202012Validator(expected).iter_errors(actual))
            if errors:
                lines = [f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}" for e in errors[:10]]
                report.fail(where, "\n".join(lines))
        elif mode == "golden":
            path = case.spec.dir / expected
            if bless:
                if _write_golden(path, (dumps(actual) + "\n").encode("utf-8")):
                    report.blessed.append(path)
            elif not path.is_file():
                report.fail(where, f"golden file {expected} not found (create it with `ut bless`)")
            else:
                e = json.loads(path.read_text(encoding="utf-8"))
                if not deep_equal(e, actual):
                    report.fail(where, f"differs from {expected}\n{_mismatch(e, actual)}")


def _check_text(case: Case, where: str, texp: Any, actual: str, report: CheckReport, bless: bool) -> None:
    if isinstance(texp, str):
        texp = {"equals": texp}
    ignore = texp.get("ignore", [])
    if "equals" in texp:
        expected = _mask(texp["equals"].replace("\r\n", "\n"), ignore)
        if expected != _mask(actual, ignore):
            report.fail(f"{where}.equals", _text_diff(expected, _mask(actual, ignore)))
    for s in texp.get("contains", []):
        if s not in actual:
            report.fail(f"{where}.contains", f"missing {s!r}{_excerpt(actual)}")
    for s in texp.get("not_contains", []):
        if s in actual:
            report.fail(f"{where}.not_contains", f"found {s!r}{_excerpt(actual)}")
    for pattern in texp.get("matches", []):
        if not re.search(pattern, actual, re.M):
            report.fail(f"{where}.matches", f"no line matches /{pattern}/{_excerpt(actual)}")
    if "empty" in texp and texp["empty"] != (actual.strip() == ""):
        report.fail(f"{where}.empty", "expected no output" + _excerpt(actual) if texp["empty"] else "expected output, got none")
    if "golden" in texp:
        rel = texp["golden"]
        path = case.spec.dir / rel
        if bless:
            if _write_golden(path, actual.encode("utf-8")):
                report.blessed.append(path)
        elif not path.is_file():
            report.fail(f"{where}.golden", f"golden file {rel} not found (create it with `ut bless`)")
        else:
            expected = _mask(path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n"), ignore)
            masked = _mask(actual, ignore)
            if expected != masked:
                report.fail(f"{where}.golden", f"differs from golden {rel}\n{_text_diff(expected, masked)}")


def _check_file(case: Case, fexp: dict[str, Any], sandbox: Path, norm: Normalizer,
                report: CheckReport, bless: bool) -> None:
    rel = fexp["path"]
    where = f"files[{rel}]"
    path = sandbox / rel
    if not path.is_file():
        report.fail(where, "exists but is not a file" if path.exists() else "was not created")
        return
    mode = fexp.get("compare", "text")
    data = path.read_bytes()
    golden_rel = fexp.get("golden")
    golden = case.spec.dir / golden_rel if golden_rel else None

    if mode == "binary":
        if golden is None:
            return
        if bless:
            if _write_golden(golden, data):
                report.blessed.append(golden)
        elif not golden.is_file():
            report.fail(f"{where}.golden", f"golden file {golden_rel} not found (create it with `ut bless`)")
        else:
            expected = golden.read_bytes()
            if expected != data:
                report.fail(f"{where}.golden", f"differs from golden {golden_rel}: "
                            f"expected {len(expected)} bytes (sha256 {_sha(expected)}), "
                            f"got {len(data)} bytes (sha256 {_sha(data)})")
        return

    text = norm.text(data.decode("utf-8", errors="replace")).replace("\r\n", "\n")
    if mode == "json":
        try:
            actual = canonicalize(json.loads(text))
        except ValueError as exc:
            report.fail(where, f"is not valid JSON: {exc}")
            return
        if golden is None:
            return
        if bless:
            if _write_golden(golden, (dumps(actual) + "\n").encode("utf-8")):
                report.blessed.append(golden)
        elif not golden.is_file():
            report.fail(f"{where}.golden", f"golden file {golden_rel} not found (create it with `ut bless`)")
        else:
            expected = canonicalize(json.loads(golden.read_text(encoding="utf-8")))
            if not deep_equal(expected, actual):
                report.fail(f"{where}.golden", f"differs from golden {golden_rel}\n{_mismatch(expected, actual)}")
        return

    texp = {k: fexp[k] for k in ("contains", "not_contains", "matches", "ignore", "golden") if k in fexp}
    _check_text(case, where, texp, text, report, bless)


def _check_custom(case: Case, ref: str, outcome: Outcome, sandbox: Path, report: CheckReport) -> None:
    module_name, func_name = ref.split(":")
    try:
        func = getattr(importlib.import_module(module_name), func_name)
    except (ImportError, AttributeError) as exc:
        report.fail("custom", f"cannot load {ref}: {type(exc).__name__}: {exc}")
        return
    try:
        result = func(outcome=outcome, case=case, sandbox=sandbox)
    except AssertionError as exc:
        report.fail("custom", str(exc) or f"{ref}: assertion failed")
        return
    except Exception as exc:  # noqa: BLE001 - a broken check is a failing check
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        report.fail("custom", f"{ref} raised {type(exc).__name__}: {exc}\n{tb}")
        return
    if result is False:
        report.fail("custom", f"{ref} returned False")
    elif isinstance(result, str) and result:
        report.fail("custom", result)


# --- helpers -----------------------------------------------------------------

def _write_golden(path: Path, content: bytes) -> bool:
    """Write `content` to `path`; True if the file was created or changed."""
    if path.is_file() and path.read_bytes() == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return True


def _mask(text: str, patterns: list[str]) -> str:
    for pattern in patterns:
        text = re.sub(pattern, IGNORED_MARK, text, flags=re.M)
    return text


def _compact(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _short(text: str, limit: int = 120) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _mismatch(expected: Any, actual: Any) -> str:
    e, a = _compact(expected), _compact(actual)
    if len(e) <= 100 and len(a) <= 100:
        return f"expected: {e}\nactual:   {a}"
    return _text_diff(dumps(expected), dumps(actual))


def _text_diff(expected: str, actual: str) -> str:
    lines = list(difflib.unified_diff(expected.splitlines(), actual.splitlines(),
                                      "expected", "actual", lineterm="", n=2))
    if not lines:
        return f"texts differ only in line endings or a trailing newline:\nexpected ends {expected[-20:]!r}\nactual ends   {actual[-20:]!r}"
    if len(lines) > MAX_DIFF_LINES:
        lines = lines[:MAX_DIFF_LINES] + [f"... ({len(lines) - MAX_DIFF_LINES} more diff lines)"]
    return "\n".join(lines)


def _excerpt(text: str, limit: int = 300) -> str:
    if not text:
        return " (output is empty)"
    return f" in:\n{_short(text, limit)}"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]
