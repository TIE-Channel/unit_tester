"""Exception types shared across the engine."""

from __future__ import annotations

from pathlib import Path


class UtError(Exception):
    """Base class for engine errors."""


class ConfigError(UtError):
    """ut.toml is missing, unreadable or invalid."""


class SpecError(UtError):
    """A test spec failed schema or semantic validation."""

    def __init__(self, path: Path, problems: list[str]):
        self.path = path
        self.problems = problems
        super().__init__(f"{path}: " + "; ".join(problems))

    def render(self) -> str:
        lines = [f"invalid test spec {self.path}:"]
        lines += [f"  - {p}" for p in self.problems]
        return "\n".join(lines)


class SetupError(UtError):
    """A case could not be set up: the procedure cannot be imported, a mock target
    does not exist. Counts as a test failure, since the code under test is at fault."""


class InfraError(UtError):
    """The test infrastructure failed (missing tool, licence, crashed worker).

    Reported separately from test failures so CI can tell broken code from a
    broken environment (CLI exit code 3).
    """
