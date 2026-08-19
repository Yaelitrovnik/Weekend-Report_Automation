from __future__ import annotations

from typing import Any

from app.collectors.base import Collector
from app.orchestrator.run_context import RunContext


class DoctorCollector(Collector):
    def collect(self, context: RunContext) -> dict[str, Any]:
        doctor = context.config.get("doctor", {}).get("doctor", {})
        if doctor.get("mode") == "manual":
            return {"mode": "manual", "manual_review": doctor.get("manual_review", {})}
        return {"mode": "api", "sites": doctor.get("fixture_actual", {})}
