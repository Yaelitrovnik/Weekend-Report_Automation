from __future__ import annotations

from typing import Any

from app.collectors.base import Collector
from app.orchestrator.run_context import RunContext


class RabbitMQCollector(Collector):
    def collect(self, context: RunContext) -> dict[str, Any]:
        config = context.config.get("rabbitmq_expected", {})
        mode = config.get("collection_mode") or (
            "fixture" if config.get("fixture_actual") is not None else "live"
        )
        actual = config.get("fixture_actual")
        if mode == "fixture" and actual is None:
            return {
                "error": "RabbitMQ fixture mode is configured without fixture_actual",
                "sites": {},
            }
        if actual is not None:
            sites = actual.get("sites", actual) if isinstance(actual, dict) else {}
            return {"mode": "fixture", "sites": sites, "errors": []}
        return {
            "error": "RabbitMQ live collection is blocked until Management API values are approved",
            "sites": {},
            "errors": [
                {
                    "code": "RABBITMQ_LIVE_BLOCKED",
                    "message": (
                        "RabbitMQ live collection requires approved Management API configuration"
                    ),
                }
            ],
        }
