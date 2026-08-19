from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from app.collectors.base import Collector
from app.orchestrator.run_context import RunContext
from app.time_utils import iso_now


@dataclass(slots=True)
class RecordingWorkflowContract:
    """Read-only description of the approved existing-device workflow boundary."""

    site: str
    selected_device_id: str | None
    webapp_baseline: int | None
    backend_baseline: int | None
    recovery_required: bool = False


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
            sites = fixture.get("sites", fixture) if isinstance(fixture, dict) else {}
            return {
                "mode": "fixture",
                "collection_timestamp": iso_now(),
                "workflow": "existing_device_start_stop",
                "sites": copy.deepcopy(sites),
                "errors": [],
            }
        return _blocked_payload(
            "RECORDING_LIVE_BLOCKED",
            (
                "Recording existing-device start/stop workflow is blocked until approved "
                "WebApp, backend, and action contracts are supplied"
            ),
        )


class ExistingDeviceRecordingWorkflow:
    """Fixture harness for the safe Recording workflow.

    Production integrations must implement the same phases without creating or deleting devices:
    baseline WebApp/backend counts, select an existing non-recording device, start recording on
    that same device, observe WebApp/backend increments, stop the same device, observe restoration,
    and report cleanup/recovery state.
    """

    mandatory_steps = [
        "device_selection",
        "webapp_baseline",
        "backend_baseline",
        "start_action",
        "device_started",
        "webapp_increment",
        "backend_increment",
        "stop_action",
        "device_stopped",
        "webapp_restored",
        "backend_restored",
        "cleanup",
    ]

    def run_fixture(self, site: str, fixture_site: dict[str, Any]) -> dict[str, Any]:
        selected = fixture_site.get("device_selection", {}).get("device", {})
        return {
            "site": site,
            "workflow": "existing_device_start_stop",
            "selected_device": copy.deepcopy(selected),
            **copy.deepcopy(fixture_site),
        }


def _blocked_payload(code: str, message: str) -> dict[str, Any]:
    return {
        "mode": "blocked",
        "collection_timestamp": iso_now(),
        "workflow": "existing_device_start_stop",
        "sites": {},
        "error": message,
        "errors": [{"code": code, "message": message, "state_changing": False}],
    }
