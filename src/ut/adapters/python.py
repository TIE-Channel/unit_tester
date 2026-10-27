"""Python adapter: imports the procedure and calls it in-process."""

from __future__ import annotations

import builtins
import contextlib
import copy
import importlib
import io
import os
import re
import sys
import time
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator
from unittest import mock as mocklib

from ut.adapters.base import Adapter, ErrorInfo, Invocation, Outcome
from ut.canonical import Normalizer, canonicalize
from ut.errors import SetupError

if TYPE_CHECKING:
    from ut.spec import Case, Spec

PROCEDURE_PATTERN = re.compile(r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*)*:[A-Za-z_]\w*(\.[A-Za-z_]\w*)*$")
_RAISES = re.compile(r"^([A-Za-z_][\w.]*(?::[A-Za-z_][\w.]*)?)(?::\s*(.*))?$", re.S)


class PythonAdapter(Adapter):
    language = "python"

    @classmethod
    def validate_spec(cls, spec: Spec) -> list[str]:
        problems = []
        if not PROCEDURE_PATTERN.match(spec.procedure):
            problems.append(f"target.procedure: expected 'package.module:function', got {spec.procedure!r}")
        if "setup" in spec.data:
            problems.append("setup: only used by SKILL specs")
        if spec.data.get("stubs") or any(c.data.get("stubs") for c in spec.cases):
            problems.append("stubs: SKILL only; use mocks for Python")
        for case in spec.cases:
            for target, m in case.mocks.items():
                if "raises" in m and not _RAISES.match(m["raises"]):
                    problems.append(f"cases ({case.id}).mocks.{target}.raises: expected 'ExceptionType: message'")
        return problems

    def start(self) -> None:
        for path in reversed(self.config.python_paths):
            if str(path) not in sys.path:
                sys.path.insert(0, str(path))

    def call(self, case: Case, invocation: Invocation) -> Outcome:
        func = _resolve(case.spec.procedure)
        normalizer = Normalizer(invocation.sandbox)
        out, err = io.StringIO(), io.StringIO()
        error = None
        raw = None
        with contextlib.ExitStack() as stack:
            for target, m in case.mocks.items():
                try:
                    stack.enter_context(_patch(target, m))
                except (AttributeError, ImportError) as exc:
                    raise SetupError(f"cannot mock {target!r}: {type(exc).__name__}: {exc}") from None
            stack.enter_context(_chdir(invocation.sandbox))
            stack.enter_context(contextlib.redirect_stdout(out))
            stack.enter_context(contextlib.redirect_stderr(err))
            start = time.perf_counter()
            try:
                raw = func(*invocation.args, **invocation.kwargs)
            except KeyboardInterrupt:
                raise
            except BaseException as exc:  # SystemExit from the code under test is a result too
                error = _error_info(exc)
            duration = time.perf_counter() - start
        return Outcome(
            returned=None if error else canonicalize(raw, normalizer),
            error=error,
            stdout=out.getvalue(),
            stderr=err.getvalue(),
            duration=duration,
            raw=raw,
        )


def _resolve(procedure: str) -> Any:
    module_name, _, attr_path = procedure.partition(":")
    try:
        obj = importlib.import_module(module_name)
    except Exception as exc:
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        raise SetupError(f"cannot import module {module_name!r}:\n{tb}") from None
    for part in attr_path.split("."):
        try:
            obj = getattr(obj, part)
        except AttributeError:
            raise SetupError(f"module {module_name!r} has no attribute {attr_path!r}") from None
    if not callable(obj):
        raise SetupError(f"{procedure!r} is not callable")
    return obj


def _patch(target: str, m: dict[str, Any]) -> Any:
    target = target.replace(":", ".")
    if "return" in m:
        return mocklib.patch(target, return_value=copy.deepcopy(m["return"]))
    if "raises" in m:
        return mocklib.patch(target, side_effect=_make_exception(m["raises"]))
    return mocklib.patch(target, new=copy.deepcopy(m["value"]))


def _make_exception(text: str) -> BaseException:
    match = _RAISES.match(text)
    if not match:
        raise SetupError(f"cannot parse exception {text!r}; expected 'ExceptionType: message'")
    name, message = match.group(1), match.group(2)
    if ":" in name:
        module_name, attr = name.split(":", 1)
    elif "." in name:
        module_name, attr = name.rsplit(".", 1)
    else:
        module_name, attr = "builtins", name
    try:
        obj: Any = builtins if module_name == "builtins" else importlib.import_module(module_name)
        for part in attr.split("."):
            obj = getattr(obj, part)
    except (ImportError, AttributeError):
        raise SetupError(f"unknown exception type {name!r}") from None
    if not (isinstance(obj, type) and issubclass(obj, BaseException)):
        raise SetupError(f"{name!r} is not an exception type")
    return obj(message) if message else obj()


def _error_info(exc: BaseException) -> ErrorInfo:
    cls = type(exc)
    types: list[str] = []
    for klass in cls.__mro__:
        if klass is object:
            continue
        types += [klass.__name__, f"{klass.__module__}.{klass.__qualname__}",
                  f"{klass.__module__}:{klass.__qualname__}"]
    # Drop the adapter's own frame so the traceback starts in the code under test.
    tb = exc.__traceback__.tb_next if exc.__traceback__ else None
    return ErrorInfo(
        type=cls.__name__,
        types=types,
        message=str(exc),
        traceback="".join(traceback.format_exception(cls, exc, tb)),
    )


@contextlib.contextmanager
def _chdir(path: Path) -> Iterator[None]:
    previous = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)
