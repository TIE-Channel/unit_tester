"""Language adapters and the per-session pool that owns them."""

from __future__ import annotations

from ut.adapters.base import Adapter, ErrorInfo, Invocation, Outcome
from ut.adapters.python import PythonAdapter
from ut.adapters.skill import SkillAdapter
from ut.config import Config
from ut.errors import InfraError

ADAPTERS: dict[str, type[Adapter]] = {cls.language: cls for cls in (PythonAdapter, SkillAdapter)}

__all__ = ["ADAPTERS", "Adapter", "AdapterPool", "ErrorInfo", "Invocation", "Outcome", "adapter_class"]


def adapter_class(language: str) -> type[Adapter]:
    return ADAPTERS[language]


class AdapterPool:
    """Creates each adapter on first use, starts it once, stops it at session end.

    A failed start is remembered, so a broken runtime fails every remaining case
    of that language immediately instead of retrying the start each time.
    """

    def __init__(self, config: Config):
        self.config = config
        self._instances: dict[str, Adapter] = {}
        self._started: set[str] = set()
        self._failed: dict[str, InfraError] = {}

    def _instance(self, language: str) -> Adapter:
        if language not in self._instances:
            self._instances[language] = adapter_class(language)(self.config)
        return self._instances[language]

    def unavailable_reason(self, language: str) -> str | None:
        return self._instance(language).unavailable_reason()

    def get(self, language: str) -> Adapter:
        if language in self._failed:
            raise self._failed[language]
        adapter = self._instance(language)
        if language not in self._started:
            try:
                adapter.start()
            except InfraError as exc:
                self._failed[language] = exc
                raise
            self._started.add(language)
        return adapter

    def stop_all(self) -> None:
        for language in sorted(self._started):
            try:
                self._instances[language].stop()
            except Exception:  # noqa: BLE001 - shutdown must not mask test results
                pass
        self._started.clear()
