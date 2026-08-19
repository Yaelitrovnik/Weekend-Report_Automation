from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.domain import CheckResult
from app.orchestrator.run_context import RunContext


class Validator(ABC):
    @abstractmethod
    def validate(
        self, actual: dict[str, Any], config: dict[str, Any], context: RunContext
    ) -> list[CheckResult]:
        raise NotImplementedError
