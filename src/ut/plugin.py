"""pytest plugin: collects `*.test.yaml` specs as test items.

Loaded automatically through the `pytest11` entry point, so `ut run`, plain
`pytest` and IDE test explorers all see the same tests. Tags become markers,
the language becomes a marker (`python`, `skill`), and procedure/language are
recorded as JUnit properties.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

import pytest

from ut.config import KEEP_POLICIES, LANGUAGES, load_config
from ut.engine import CaseResult, Session
from ut.errors import ConfigError, InfraError, SetupError, SpecError
from ut.sandbox import prune_empty_dirs, reset_root
from ut.spec import Case, is_spec_file, load_spec

SESSION_KEY = pytest.StashKey[Session]()
MARKERS_KEY = pytest.StashKey[set]()
INFRA_PROPERTY = "ut_infra_error"
BLESSED_PROPERTY = "ut_blessed"
_OUTPUT_TAIL_LINES = 30


class CaseFailed(Exception):
    def __init__(self, result: CaseResult):
        super().__init__(f"{len(result.failures)} check(s) failed")
        self.result = result


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("ut", "ut test specs")
    group.addoption("--ut-config", metavar="PATH",
                    help="ut.toml to use (default: the nearest one at or above the current directory)")
    group.addoption("--ut-bless", action="store_true",
                    help="write current outputs to the golden files instead of comparing against them")
    group.addoption("--ut-lang", action="append", choices=LANGUAGES,
                    help="only run tests for this language (repeatable)")
    group.addoption("--ut-keep-sandbox", choices=KEEP_POLICIES,
                    help="which case sandboxes to keep: failed (default), always, never")


def pytest_configure(config: pytest.Config) -> None:
    try:
        ut_config = load_config(config.getoption("ut_config"), start=config.invocation_params.dir)
    except ConfigError as exc:
        raise pytest.UsageError(str(exc)) from None
    config.stash[SESSION_KEY] = Session(
        ut_config, bless=config.getoption("ut_bless"), keep_sandbox=config.getoption("ut_keep_sandbox"))
    config.stash[MARKERS_KEY] = set(LANGUAGES)
    for language in LANGUAGES:
        config.addinivalue_line("markers", f"{language}: test of a {language} procedure")
    if not _is_xdist_worker(config):
        # Runs before xdist starts its workers, so no case sandbox exists yet.
        reset_root(ut_config.sandbox_root)


def pytest_unconfigure(config: pytest.Config) -> None:
    session = config.stash.get(SESSION_KEY, None)
    if session is not None:
        session.close()
        if not _is_xdist_worker(config):
            prune_empty_dirs(session.config.sandbox_root)


def _is_xdist_worker(config: pytest.Config) -> bool:
    return hasattr(config, "workerinput")


@pytest.hookimpl(tryfirst=True)
def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool | None:
    session = config.stash.get(SESSION_KEY, None)
    if session is None:
        return None
    path = collection_path.resolve()
    out_dir = session.config.out_dir.resolve()
    if path == out_dir or out_dir in path.parents:
        return True
    # pytest's default norecursedirs skips directories named build, dist, venv...
    # Under the ut test roots the layout is ut's, and spec directories are
    # named after procedures, which may well be called `build`.
    if _is_default_norecurse_name(path.name) and path.is_dir():
        roots = [p.resolve() for p in session.config.tests]
        explicitly_ignored = {Path(p).resolve() for p in config.getoption("ignore", None) or []}
        if any(root in path.parents for root in roots) and path not in explicitly_ignored:
            return False
    return None


def _is_default_norecurse_name(name: str) -> bool:
    return name in {"build", "dist", "venv", "CVS", "_darcs", "{arch}"} or name.endswith(".egg")


def pytest_collect_file(file_path: Path, parent: pytest.Collector) -> pytest.Collector | None:
    if is_spec_file(file_path):
        return SpecFile.from_parent(parent, path=file_path)
    return None


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    languages = config.getoption("ut_lang")
    if not languages:
        return
    keep, drop = [], []
    for item in items:
        # Hand-written pytest tests are Python tests.
        language = item.case.spec.language if isinstance(item, CaseItem) else "python"
        (keep if language in languages else drop).append(item)
    if drop:
        config.hook.pytest_deselected(items=drop)
        items[:] = keep


def pytest_terminal_summary(terminalreporter: Any, exitstatus: int, config: pytest.Config) -> None:
    blessed: set[str] = set()
    infra: dict[str, str] = {}
    for reports in terminalreporter.stats.values():
        for report in reports:
            for name, value in getattr(report, "user_properties", None) or []:
                if name == BLESSED_PROPERTY:
                    blessed.add(value)
                elif name == INFRA_PROPERTY:
                    infra[report.nodeid] = value
    if blessed:
        terminalreporter.write_sep("-", f"ut: {len(blessed)} golden file(s) written")
        for path in sorted(blessed):
            terminalreporter.write_line(f"  {path}")
        terminalreporter.write_line("Review the changes (git diff) before committing them.")
    if infra:
        terminalreporter.write_sep("!", f"ut: {len(infra)} infrastructure error(s)")
        for nodeid, message in sorted(infra.items()):
            terminalreporter.write_line(f"  {nodeid}: {message}")


class SpecFile(pytest.File):
    def collect(self) -> Iterator[pytest.Item]:
        session = self.config.stash[SESSION_KEY]
        spec = load_spec(self.path, allow_missing_golden=session.bless)
        for case in spec.cases:
            yield CaseItem.from_parent(self, name=case.id, case=case)

    def repr_failure(self, excinfo: pytest.ExceptionInfo[BaseException], **kwargs: Any) -> Any:
        if isinstance(excinfo.value, SpecError):
            return excinfo.value.render()
        return super().repr_failure(excinfo, **kwargs)


class CaseItem(pytest.Item):
    def __init__(self, *, case: Case, **kwargs: Any):
        super().__init__(**kwargs)
        self.case = case
        self._mark(case.spec.language)
        for tag in case.tags:
            self._mark(tag)
        skip = case.skip or self.config.stash[SESSION_KEY].skip_reason(case)
        if skip:
            self.add_marker(pytest.mark.skip(reason=skip))
        if case.xfail:
            # strict: a case that starts passing must have its xfail removed
            self.add_marker(pytest.mark.xfail(reason=case.xfail, strict=True))
        if case.timeout and self.config.pluginmanager.hasplugin("timeout"):
            self.add_marker(pytest.mark.timeout(case.timeout))
        self.user_properties.append(("procedure", case.spec.procedure))
        self.user_properties.append(("language", case.spec.language))

    def _mark(self, name: str) -> None:
        registered = self.config.stash[MARKERS_KEY]
        if name not in registered:
            self.config.addinivalue_line("markers", f"{name}: ut tag")
            registered.add(name)
        self.add_marker(name)

    def runtest(self) -> None:
        session = self.config.stash[SESSION_KEY]
        try:
            result = session.run(self.case)
        except InfraError as exc:
            self.user_properties.append((INFRA_PROPERTY, str(exc)))
            raise
        for path in result.blessed:
            self.user_properties.append((BLESSED_PROPERTY, session.config.rel(path)))
        if result.failures:
            raise CaseFailed(result)

    def repr_failure(self, excinfo: pytest.ExceptionInfo[BaseException], style: Any = None) -> Any:
        # The first line is what pytest's short summary shows, so it names what failed.
        exc = excinfo.value
        if isinstance(exc, CaseFailed):
            return self._render_result(exc.result)
        if isinstance(exc, SetupError):
            return f"SETUP ERROR: {exc}\n{self._header()}"
        if isinstance(exc, InfraError):
            return f"INFRASTRUCTURE ERROR: {exc}\n{self._header()}"
        return super().repr_failure(excinfo, style=style)

    def reportinfo(self) -> tuple[Path, int | None, str]:
        line = self.case.line - 1 if self.case.line else None
        return self.path, line, f"{self.case.spec.procedure} [{self.case.id}]"

    def _header(self) -> str:
        cfg = self.config.stash[SESSION_KEY].config
        spec = self.case.spec
        location = cfg.rel(spec.path) + (f":{self.case.line}" if self.case.line else "")
        lines = [f"spec:    {location}  ({spec.language})"]
        if self.case.description:
            lines.append(f"about:   {self.case.description}")
        return "\n".join(lines)

    def _render_result(self, result: CaseResult) -> str:
        cfg = self.config.stash[SESSION_KEY].config
        sandbox = cfg.rel(result.sandbox) if result.sandbox_kept else "(removed)"
        summary = "failed: " + ", ".join(f.where for f in result.failures)
        lines = [summary, self._header(), f"sandbox: {sandbox}", ""]
        for failure in result.failures:
            lines.append(f"FAILED {failure.where}")
            lines += [f"  {line}" for line in failure.message.splitlines()]
            lines.append("")
        for stream in ("stdout", "stderr"):
            text = getattr(result.outcome, stream)
            if text.strip():
                tail = text.rstrip().splitlines()[-_OUTPUT_TAIL_LINES:]
                lines.append(f"--- captured {stream} (last {len(tail)} lines) ---")
                lines += tail
        return "\n".join(lines).rstrip()
