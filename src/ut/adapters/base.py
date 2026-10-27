"""The interface every language adapter implements.

An adapter knows how to call one procedure in its language and report what
happened in canonical form (see ut.canonical). Everything else (sandboxes,
checks, goldens, reporting) is shared.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from ut.config import Config
    from ut.spec import Case, Spec


@dataclass
class ErrorInfo:
    type: str
    # Names `expect.error.type` may use: class names and qualified names along
    # the class hierarchy, so a base class matches its subclasses.
    types: list[str] = field(default_factory=list)
    message: str = ""
    traceback: str = ""


@dataclass
class Outcome:
    returned: Any = None  # canonical form; None when the call raised
    error: ErrorInfo | None = None
    stdout: str = ""
    stderr: str = ""
    duration: float = 0.0
    raw: Any = None  # the language-native return value, when available


@dataclass
class Invocation:
    args: list[Any]
    kwargs: dict[str, Any]
    sandbox: Path
    timeout: float | None = None


class Adapter:
    language: ClassVar[str] = ""

    def __init__(self, config: Config):
        self.config = config

    @classmethod
    def validate_spec(cls, spec: Spec) -> list[str]:
        """Language-specific spec problems, checked by `ut validate`."""
        return []

    def unavailable_reason(self) -> str | None:
        """Why this adapter cannot run here (cases are skipped), or None."""
        return None

    def start(self) -> None:
        """Called once before the first case; raise InfraError if the runtime is broken."""

    def stop(self) -> None:
        """Called once at the end of the session."""

    def call(self, case: Case, invocation: Invocation) -> Outcome:
        raise NotImplementedError
