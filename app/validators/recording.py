from __future__ import annotations

from typing import Any

from app.domain import CheckResult, CheckStatus
from app.orchestrator.run_context import RunContext
from app.time_utils import iso_now
from app.validators.base import Validator

MANDATORY_STEPS = [
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


class RecordingValidator(Validator):
    def validate(
        self, actual: dict[str, Any], config: dict[str, Any], context: RunContext
    ) -> list[CheckResult]:
        started = iso_now()
        results: list[CheckResult] = []
        if actual.get("error"):
            results.append(
                _r(
                    context.run_id,
                    "safety_block",
                    None,
                    "recording",
                    {"workflow": "existing_device_start_stop", "state_changing": False},
                    actual,
                    CheckStatus.ERROR,
                    actual["error"],
                    started,
                    metadata={"blocked_live_execution": True},
                )
            )
            return results
        for site, observed in actual.get("sites", {}).items():
            if not isinstance(observed, dict):
                results.append(
                    _r(
                        context.run_id,
                        "collection",
                        site,
                        site,
                        {"site": site},
                        observed,
                        CheckStatus.ERROR,
                        "Recording actual state for site is malformed",
                        started,
                    )
                )
                continue
            site_results = _validate_site(
                context.run_id,
                site,
                observed,
                config.get("recording", {}).get("sites", {}).get(site, {}),
                started,
            )
            results.extend(site_results)
        return results


def _validate_site(
    run_id: str,
    site: str,
    observed: dict[str, Any],
    site_config: dict[str, Any],
    started: str,
) -> list[CheckResult]:
    selected_device = _selected_device(observed)
    target = selected_device.get("id") or selected_device.get("name") or site
    results = [
        _phase_result(
            run_id,
            site,
            target,
            "device_selection",
            observed.get("device_selection", {}),
            started,
            expected={"existing_device": True, "must_not_create_or_delete_device": True},
            site_config=site_config,
        ),
        _phase_result(
            run_id,
            site,
            target,
            "webapp_baseline",
            observed.get("webapp_baseline", {}),
            started,
            expected={"baseline_count": "N"},
            site_config=site_config,
        ),
        _phase_result(
            run_id,
            site,
            target,
            "backend_baseline",
            observed.get("backend_baseline", {}),
            started,
            expected={"baseline_count": "M"},
            site_config=site_config,
        ),
        _phase_result(
            run_id,
            site,
            target,
            "start_action",
            observed.get("start_action", {}),
            started,
            expected={"action": "start recording on selected existing device"},
            site_config=site_config,
        ),
        _phase_result(
            run_id,
            site,
            target,
            "device_started",
            observed.get("device_started", {}),
            started,
            expected={"recording": True},
            site_config=site_config,
            value_key="recording",
            expected_value=True,
        ),
        _phase_result(
            run_id,
            site,
            target,
            "webapp_increment",
            observed.get("webapp_increment", {}),
            started,
            expected={"count": _baseline_plus_one(observed.get("webapp_baseline", {}))},
            site_config=site_config,
        ),
        _phase_result(
            run_id,
            site,
            target,
            "backend_increment",
            observed.get("backend_increment", {}),
            started,
            expected={"count": _baseline_plus_one(observed.get("backend_baseline", {}))},
            site_config=site_config,
        ),
        _phase_result(
            run_id,
            site,
            target,
            "stop_action",
            observed.get("stop_action", {}),
            started,
            expected={"action": "stop recording on selected existing device"},
            site_config=site_config,
        ),
        _phase_result(
            run_id,
            site,
            target,
            "device_stopped",
            observed.get("device_stopped", {}),
            started,
            expected={"recording": False},
            site_config=site_config,
            value_key="recording",
            expected_value=False,
        ),
        _phase_result(
            run_id,
            site,
            target,
            "webapp_restored",
            observed.get("webapp_restored", {}),
            started,
            expected={"count": _baseline_count(observed.get("webapp_baseline", {}))},
            site_config=site_config,
        ),
        _phase_result(
            run_id,
            site,
            target,
            "backend_restored",
            observed.get("backend_restored", {}),
            started,
            expected={"count": _baseline_count(observed.get("backend_baseline", {}))},
            site_config=site_config,
        ),
        _phase_result(
            run_id,
            site,
            target,
            "cleanup",
            observed.get("cleanup", {}),
            started,
            expected={
                "selected_device_recording": False,
                "no_created_device": True,
                "no_delete_action": True,
            },
            site_config=site_config,
        ),
    ]
    module_status = _aggregate_module_status(results, observed)
    results.append(
        _r(
            run_id,
            "module_status",
            site,
            target,
            {
                "mandatory_steps": MANDATORY_STEPS,
                "pass_requires_all_mandatory": True,
                "no_create_delete_device": True,
            },
            {
                "selected_device": selected_device,
                "recovery_required": bool(observed.get("recovery_required")),
                "step_statuses": {result.check_id: result.status.value for result in results},
            },
            module_status,
            "Recording module status from mandatory existing-device workflow",
            started,
            metadata={"recovery_required": bool(observed.get("recovery_required"))},
        )
    )
    return results


def _phase_result(
    run_id: str,
    site: str,
    target: str,
    phase: str,
    actual: Any,
    started: str,
    *,
    expected: dict[str, Any],
    site_config: dict[str, Any],
    value_key: str | None = None,
    expected_value: Any = None,
) -> CheckResult:
    status, message = _phase_status(phase, actual, site_config, value_key, expected_value)
    return _r(
        run_id,
        phase,
        site,
        target,
        expected,
        actual,
        status,
        message,
        started,
        metadata={
            "workflow": "existing_device_start_stop",
            "state_changing": phase in {"start_action", "stop_action"},
        },
    )


def _phase_status(
    phase: str,
    actual: Any,
    site_config: dict[str, Any],
    value_key: str | None,
    expected_value: Any,
) -> tuple[CheckStatus, str]:
    if not isinstance(actual, dict):
        return CheckStatus.ERROR, f"{phase} result is missing or malformed"
    if actual.get("status") in {"ERROR", "FAIL", "WARNING", "SKIPPED", "PASS"}:
        status = CheckStatus(actual["status"])
        return status, str(actual.get("message") or _default_message(phase, status))
    if phase == "device_selection" and actual.get("reason") == "no_eligible_device":
        configured = (
            site_config.get("device_selection", {}).get("no_eligible_device_status")
            or site_config.get("no_eligible_device_status")
            or "ERROR"
        )
        try:
            status = CheckStatus(configured)
        except ValueError:
            status = CheckStatus.ERROR
        return status, "no suitable existing non-recording device was available"
    if actual.get("success") is not True:
        if actual.get("reliable") is False or actual.get("error") or actual.get("error_type"):
            return CheckStatus.ERROR, f"{phase} could not be determined reliably"
        return CheckStatus.FAIL, f"{phase} failed"
    if value_key is not None and actual.get(value_key) != expected_value:
        return CheckStatus.FAIL, f"{phase} observed {value_key} did not match expected"
    expected_count = actual.get("expected_count", actual.get("expected"))
    if expected_count is not None and actual.get("count") != expected_count:
        return CheckStatus.FAIL, f"{phase} count did not match expected"
    if phase == "cleanup" and actual.get("complete") is False:
        return CheckStatus.FAIL, "cleanup did not complete"
    return CheckStatus.PASS, f"{phase} passed"


def _aggregate_module_status(results: list[CheckResult], observed: dict[str, Any]) -> CheckStatus:
    if observed.get("recovery_required"):
        return CheckStatus.ERROR
    statuses = [result.status for result in results]
    if CheckStatus.ERROR in statuses:
        return CheckStatus.ERROR
    if CheckStatus.FAIL in statuses:
        return CheckStatus.FAIL
    if CheckStatus.WARNING in statuses:
        return CheckStatus.WARNING
    if CheckStatus.SKIPPED in statuses:
        return CheckStatus.SKIPPED
    return CheckStatus.PASS


def _selected_device(observed: dict[str, Any]) -> dict[str, Any]:
    selection = observed.get("device_selection", {})
    if isinstance(selection, dict) and isinstance(selection.get("device"), dict):
        return selection["device"]
    selected = observed.get("selected_device")
    return selected if isinstance(selected, dict) else {}


def _baseline_count(actual: Any) -> int | None:
    if not isinstance(actual, dict):
        return None
    value = actual.get("count")
    return int(value) if isinstance(value, int) else None


def _baseline_plus_one(actual: Any) -> int | None:
    baseline = _baseline_count(actual)
    return baseline + 1 if baseline is not None else None


def _default_message(phase: str, status: CheckStatus) -> str:
    return f"{phase} {status.value.lower()}"


def _r(
    run_id: str,
    check: str,
    site: str | None,
    target: str | None,
    expected: Any,
    actual: Any,
    status: CheckStatus,
    message: str,
    started: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> CheckResult:
    return CheckResult(
        run_id,
        "recording",
        f"recording.{check}",
        status,
        message,
        site=site,
        target=target,
        expected=expected,
        actual=actual,
        started_at=started,
        finished_at=iso_now(),
        metadata=metadata or {},
    )
