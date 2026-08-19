from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.orchestrator.run_context import RunContext


class Collector(ABC):
    @abstractmethod
    def collect(self, context: RunContext) -> dict[str, Any]:
        raise NotImplementedError
