"""Runs single cases end to end: sandbox, adapter call, checks, cleanup.

Independent of pytest, so the plugin and a future GUI share one code path.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ut.adapters import AdapterPool, Invocation, Outcome
from ut.checks import Failure, check_outcome
from ut.config import Config
from ut.sandbox import ensure_root, prepare, remove, resolve_arguments, sandbox_path
from ut.spec import Case

RESULT_FILE = "_ut_result.json"


@dataclass
class CaseResult:
    case: Case
    sandbox: Path
    outcome: Outcome
    failures: list[Failure] = field(default_factory=list)
    blessed: list[Path] = field(default_factory=list)
    sandbox_kept: bool = True

    @property
    def passed(self) -> bool:
        return not self.failures


class Session:
    def __init__(self, config: Config, *, bless: bool = False, keep_sandbox: str | None = None):
        self.config = config
        self.bless = bless
        self.keep_sandbox = keep_sandbox or config.keep_sandbox
        self.pool = AdapterPool(config)
        # Custom checks are Python callables for every language, so the
        # configured import paths apply to the whole session.
        for path in reversed(config.python_paths):
            if str(path) not in sys.path:
                sys.path.insert(0, str(path))

    def skip_reason(self, case: Case) -> str | None:
        return self.pool.unavailable_reason(case.spec.language)

    def run(self, case: Case) -> CaseResult:
        """Raises SetupError or InfraError when the case cannot run at all."""
        adapter = self.pool.get(case.spec.language)
        ensure_root(self.config.sandbox_root)
        sandbox = prepare(sandbox_path(self.config, case))
        args, kwargs = resolve_arguments(case, sandbox)
        outcome = adapter.call(case, Invocation(args, kwargs, sandbox, case.timeout))
        report = check_outcome(case, outcome, sandbox, bless=self.bless)
        result = CaseResult(case, sandbox, outcome, report.failures, report.blessed)
        if self.keep_sandbox == "never" or (self.keep_sandbox == "failed" and result.passed):
            remove(sandbox)
            result.sandbox_kept = False
        else:
            self._write_result(result, args, kwargs)
        return result

    def _write_result(self, result: CaseResult, args: list, kwargs: dict) -> None:
        """Leave a record of the case next to its outputs, for debugging and tools."""
        case, outcome = result.case, result.outcome
        record = {
            "spec": self.config.rel(case.spec.path),
            "case": case.id,
            "procedure": case.spec.procedure,
            "language": case.spec.language,
            "passed": result.passed,
            "args": args,
            "kwargs": kwargs,
            "failures": [{"where": f.where, "message": f.message} for f in result.failures],
            "returned": outcome.returned,
            "error": dataclasses.asdict(outcome.error) if outcome.error else None,
            "stdout": outcome.stdout,
            "stderr": outcome.stderr,
            "duration": round(outcome.duration, 6),
        }
        (result.sandbox / RESULT_FILE).write_text(
            json.dumps(record, indent=2, default=str, ensure_ascii=False), encoding="utf-8")

    def close(self) -> None:
        self.pool.stop_all()
