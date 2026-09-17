from __future__ import annotations

import copy
from typing import Any

from app.collectors.base import Collector
from app.orchestrator.run_context import RunContext
from app.time_utils import iso_now


class RecordingCollector(Collector):
    def collect(self, context: RunContext) -> dict[str, Any]:
        config = context.config.get("recording", {})
        mode = config.get("collection_mode") or (
            "fixture" if config.get("fixture_actual") is not None else "live"
        )
        fixture = config.get("fixture_actual")
        if mode == "fixture":
            if fixture is None:
                return _blocked_payload(
                    "RECORDING_FIXTURE_MISSING",
                    "Recording fixture mode is configured without fixture_actual",
                )
            return {
                "mode": "fixture",
                "collection_timestamp": iso_now(),
                "workflow": "existing_device_start_stop",
                **copy.deepcopy(fixture),
                "errors": [],
            }
        return _blocked_payload(
            "RECORDING_LIVE_BLOCKED",
            (
                "Recording Manager existing-device start/stop workflow is blocked until "
                "approved Manager control and four observation-point read contracts are supplied"
            ),
        )


def _blocked_payload(code: str, message: str) -> dict[str, Any]:
    return {
        "mode": "blocked",
        "collection_timestamp": iso_now(),
        "workflow": "existing_device_start_stop",
        "selected_device": None,
        "observations": {},
        "error": message,
        "errors": [{"code": code, "message": message, "state_changing": False}],
    }
