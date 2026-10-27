"""SKILL adapter (placeholder until Phase 1).

SKILL specs are already validated, listed and collected, and are reported as
skipped at run time, so the format can be adopted and reviewed before the
Virtuoso worker exists. Phase 1 replaces `unavailable_reason` and `call` with a
persistent `virtuoso -nograph` worker.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ut.adapters.base import Adapter
from ut.spec import iter_tokens

if TYPE_CHECKING:
    from ut.spec import Spec

PROCEDURE_PATTERN = re.compile(r"^[A-Za-z_][\w.]*$")


class SkillAdapter(Adapter):
    language = "skill"

    @classmethod
    def validate_spec(cls, spec: Spec) -> list[str]:
        problems = []
        if not PROCEDURE_PATTERN.match(spec.procedure):
            problems.append(f"target.procedure: {spec.procedure!r} is not a SKILL procedure name")
        if spec.data.get("mocks") or any(c.data.get("mocks") for c in spec.cases):
            problems.append("mocks: Python only; use stubs (SKILL expressions) for SKILL")
        for rel in spec.data.get("setup", {}).get("load", []):
            if ".." in rel.split("/"):
                problems.append(f"setup.load: {rel!r} must be relative to the repository root without '..'")
        for case in spec.cases:
            for loc, token in iter_tokens(case):
                if "as" in token:
                    problems.append(f"cases ({case.id}).{loc}: 'as' is Python only")
        return problems

    def unavailable_reason(self) -> str | None:
        return "SKILL adapter not implemented yet (Phase 1); spec validated only"
