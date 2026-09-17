from __future__ import annotations

from typing import Any

from app.domain import CheckResult, CheckStatus
from app.orchestrator.run_context import RunContext
from app.time_utils import iso_now
from app.validators.base import Validator

OBSERVATION_POINTS = (
    "site1_webapp",
    "site2_webapp",
    "site1_server",
    "site2_server",
)
START_DELTA_KEYS = {
    "site1_webapp": "site1_webapp_count_delta",
    "site2_webapp": "site2_webapp_count_delta",
    "site1_server": "site1_server_count_delta",
    "site2_server": "site2_server_count_delta",
}
STOP_RETURN_KEYS = {
    "site1_webapp": "site1_webapp_must_return_to_baseline",
    "site2_webapp": "site2_webapp_must_return_to_baseline",
    "site1_server": "site1_server_must_return_to_baseline",
    "site2_server": "site2_server_must_return_to_baseline",
}


class RecordingValidator(Validator):
    def validate(
        self, actual: dict[str, Any], config: dict[str, Any], context: RunContext
    ) -> list[CheckResult]:
        started = iso_now()
        if actual.get("error"):
            return [
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
            ]

        recording_config = config.get("recording", {})
        results = _validate_workflow(context.run_id, actual, recording_config, started)
        recovery_required = any(result.metadata.get("recovery_required") for result in results)
        module_status = _aggregate_module_status(results, recovery_required)
        results.append(
            _r(
                context.run_id,
                "module_status",
                None,
                _selected_device_label(actual),
                {
                    "workflow": "existing_device_start_stop",
                    "observation_points": list(OBSERVATION_POINTS),
                    "pass_requires": "baseline, start +1, stop baseline restoration",
                },
                {
                    "selected_device": actual.get("selected_device"),
                    "recovery_required": recovery_required,
                    "step_statuses": {result.check_id: result.status.value for result in results},
                },
                module_status,
                "Recording module status from Manager start/stop workflow",
                started,
                metadata={
                    "recovery_required": recovery_required,
                    "cleanup_required": recovery_required,
                },
            )
        )
        return results


def _validate_workflow(
    run_id: str,
    actual: dict[str, Any],
    recording_config: dict[str, Any],
    started: str,
) -> list[CheckResult]:
    results = []
    selected_device = actual.get("selected_device")
    results.append(
        _device_selection_result(run_id, selected_device, actual, recording_config, started)
    )
    results.append(_pre_start_result(run_id, selected_device, actual, started))
    results.extend(_baseline_results(run_id, actual, recording_config, started))
    start_known = _action_known_succeeded(actual.get("start_action"))
    results.append(
        _action_result(
            run_id,
            "start_action",
            selected_device,
            actual.get("start_action"),
            "start recording on selected existing device",
            started,
            recovery_on_unknown=True,
        )
    )
    results.extend(
        _transition_results(
            run_id,
            "after_start",
            actual,
            recording_config,
            started,
            state_changed=start_known,
        )
    )
    results.append(
        _action_result(
            run_id,
            "stop_action",
            selected_device,
            actual.get("stop_action"),
            "stop recording on selected existing device",
            started,
            recovery_on_unknown=start_known,
            recovery_on_failure=start_known,
        )
    )
    results.extend(
        _transition_results(
            run_id,
            "after_stop",
            actual,
            recording_config,
            started,
            state_changed=start_known,
        )
    )
    results.append(_cleanup_result(run_id, selected_device, actual.get("cleanup"), started))
    return results


def _device_selection_result(
    run_id: str,
    selected_device: Any,
    actual: dict[str, Any],
    recording_config: dict[str, Any],
    started: str,
) -> CheckResult:
    selection = actual.get("device_selection", {})
    if isinstance(selection, dict) and selection.get("reason") == "no_eligible_device":
        configured = (
            recording_config.get("manager", {})
            .get("device_selection", {})
            .get("no_eligible_device_status", "ERROR")
        )
        return _r(
            run_id,
            "device_selection",
            None,
            "manager",
            {"existing_device": True, "required_initial_state": "not_recording"},
            selection,
            _status(configured, CheckStatus.ERROR),
            "no suitable existing non-recording device was available",
            started,
        )
    if not isinstance(selected_device, dict):
        return _r(
            run_id,
            "device_selection",
            None,
            "manager",
            {"existing_device": True, "required_initial_state": "not_recording"},
            selected_device,
            CheckStatus.ERROR,
            "Recording selected device was not collected reliably",
            started,
            metadata={"error_code": "RECORDING_COLLECTION_ERROR"},
        )
    if selected_device.get("created") is True or selected_device.get("deleted") is True:
        return _r(
            run_id,
            "device_selection",
            None,
            _selected_device_label({"selected_device": selected_device}),
            {"existing_device": True, "create_delete_allowed": False},
            selected_device,
            CheckStatus.ERROR,
            "Recording workflow attempted to create or delete a device",
            started,
            metadata={"error_code": "RECORDING_SAFETY_VIOLATION"},
        )
    if selected_device.get("recording") is not False:
        return _r(
            run_id,
            "device_selection",
            None,
            _selected_device_label({"selected_device": selected_device}),
            {"recording": False},
            selected_device,
            CheckStatus.ERROR,
            "selected device was not confirmed to be non-recording",
            started,
            metadata={"error_code": "RECORDING_NO_SAFE_DEVICE"},
        )
    return _r(
        run_id,
        "device_selection",
        None,
        _selected_device_label({"selected_device": selected_device}),
        {"existing_device": True, "recording": False},
        selected_device,
        CheckStatus.PASS,
        "selected existing non-recording device",
        started,
    )


def _pre_start_result(
    run_id: str,
    selected_device: Any,
    actual: dict[str, Any],
    started: str,
) -> CheckResult:
    verification = actual.get("pre_start_verification")
    if not isinstance(verification, dict):
        return _r(
            run_id,
            "pre_start_verification",
            None,
            _selected_device_label(actual),
            {"recording": False},
            verification,
            CheckStatus.ERROR,
            "pre-start recording state was not collected reliably",
            started,
            metadata={"error_code": "RECORDING_COLLECTION_ERROR"},
        )
    if verification.get("success") is not True:
        return _phase_error_or_fail(
            run_id,
            "pre_start_verification",
            _selected_device_label(actual),
            {"recording": False},
            verification,
            started,
        )
    status = CheckStatus.PASS if verification.get("recording") is False else CheckStatus.ERROR
    return _r(
        run_id,
        "pre_start_verification",
        None,
        _selected_device_label({"selected_device": selected_device}),
        {"recording": False},
        verification,
        status,
        "selected device remained non-recording immediately before START"
        if status == CheckStatus.PASS
        else "selected device was recording immediately before START",
        started,
        metadata={"error_code": "RECORDING_NO_SAFE_DEVICE"}
        if status == CheckStatus.ERROR
        else None,
    )


def _baseline_results(
    run_id: str,
    actual: dict[str, Any],
    recording_config: dict[str, Any],
    started: str,
) -> list[CheckResult]:
    observations = _observations(actual)
    baseline = observations.get("baseline", {})
    results = []
    for point in OBSERVATION_POINTS:
        point_actual = baseline.get(point) if isinstance(baseline, dict) else None
        if not _valid_count(point_actual):
            results.append(
                _r(
                    run_id,
                    f"baseline.{point}",
                    _site_for_point(point),
                    point,
                    {"capture_at_test_start": True},
                    point_actual,
                    CheckStatus.ERROR,
                    f"{_point_label(point)} baseline count was not collected reliably",
                    started,
                    metadata={"error_code": "RECORDING_COLLECTION_ERROR"},
                )
            )
            continue
        results.append(
            _r(
                run_id,
                f"baseline.{point}",
                _site_for_point(point),
                point,
                {"capture_at_test_start": True},
                point_actual,
                CheckStatus.PASS,
                f"{_point_label(point)} baseline captured",
                started,
            )
        )
    return results


def _transition_results(
    run_id: str,
    phase: str,
    actual: dict[str, Any],
    recording_config: dict[str, Any],
    started: str,
    *,
    state_changed: bool,
) -> list[CheckResult]:
    observations = _observations(actual)
    baseline = observations.get("baseline", {})
    phase_values = observations.get(phase, {})
    results = []
    for point in OBSERVATION_POINTS:
        baseline_count = _count(
            baseline.get(point) if isinstance(baseline, dict) else None
        )
        point_actual = phase_values.get(point) if isinstance(phase_values, dict) else None
        actual_count = _count(point_actual)
        recovery = (
            state_changed
            and phase == "after_stop"
            and actual_count is None
        )
        if baseline_count is None or actual_count is None:
            results.append(
                _r(
                    run_id,
                    f"{phase}.{point}",
                    _site_for_point(point),
                    point,
                    _expected_transition(phase, point, baseline_count, recording_config),
                    point_actual,
                    CheckStatus.ERROR,
                    f"{_point_label(point)} {phase} count was not collected reliably",
                    started,
                    metadata={
                        "error_code": "RECORDING_COLLECTION_ERROR",
                        "recovery_required": recovery,
                        "cleanup_required": recovery,
                    },
                )
            )
            continue
        expected_count = _expected_count(phase, point, baseline_count, recording_config)
        matched = actual_count == expected_count
        recovery = state_changed and phase == "after_stop" and not matched
        status = CheckStatus.PASS if matched else CheckStatus.FAIL
        if recovery:
            status = CheckStatus.ERROR
        results.append(
            _r(
                run_id,
                f"{phase}.{point}",
                _site_for_point(point),
                point,
                _expected_transition(phase, point, baseline_count, recording_config),
                point_actual,
                status,
                f"{_point_label(point)} {phase} matched expected count"
                if status == CheckStatus.PASS
                else f"{_point_label(point)} {phase} count mismatch",
                started,
                metadata={
                    "error_code": "RECORDING_RECOVERY_REQUIRED" if recovery else None,
                    "recovery_required": recovery,
                    "cleanup_required": recovery,
                },
            )
        )
    return results


def _action_result(
    run_id: str,
    phase: str,
    selected_device: Any,
    actual: Any,
    action: str,
    started: str,
    *,
    recovery_on_unknown: bool = False,
    recovery_on_failure: bool = False,
) -> CheckResult:
    target = _selected_device_label({"selected_device": selected_device})

    if not isinstance(actual, dict):
        return _r(
            run_id,
            phase,
            None,
            target,
            {"action": action, "same_device": True},
            actual,
            CheckStatus.ERROR,
            f"{phase} result was not collected reliably",
            started,
            metadata={
                "error_code": "RECORDING_CONTROL_ERROR",
                "recovery_required": recovery_on_unknown,
                "cleanup_required": recovery_on_unknown,
            },
        )

    if actual.get("success") is True:
        selected_id = _device_id(selected_device)
        same_device = (
            selected_id is not None and actual.get("device_id", selected_id) == selected_id
        )

        if same_device:
            return _r(
                run_id,
                phase,
                None,
                target,
                {"action": action, "same_device": True},
                actual,
                CheckStatus.PASS,
                f"{phase} succeeded on selected device",
                started,
            )

        return _r(
            run_id,
            phase,
            None,
            target,
            {"action": action, "same_device": True},
            actual,
            CheckStatus.ERROR,
            f"{phase} did not target the selected device",
            started,
            metadata={
                "error_code": "RECORDING_SAFETY_VIOLATION",
                "recovery_required": True,
                "cleanup_required": True,
            },
        )

    technical = bool(
        actual.get("reliable") is False or actual.get("error") or actual.get("error_type")
    )

    recovery = recovery_on_failure or (recovery_on_unknown and technical)

    if technical:
        status = CheckStatus.ERROR
        message = f"{phase} could not be determined reliably"
    elif recovery:
        status = CheckStatus.ERROR
        message = f"{phase} failed; recovery is required"
    else:
        status = CheckStatus.FAIL
        message = f"{phase} failed"

    return _r(
        run_id,
        phase,
        None,
        target,
        {"action": action, "same_device": True},
        actual,
        status,
        message,
        started,
        metadata={
            "error_code": (
                "RECORDING_RECOVERY_REQUIRED"
                if recovery
                else "RECORDING_CONTROL_ERROR"
                if technical
                else None
            ),
            "recovery_required": recovery,
            "cleanup_required": recovery,
        },
    )


def _cleanup_result(
    run_id: str,
    selected_device: Any,
    cleanup: Any,
    started: str,
) -> CheckResult:
    target = _selected_device_label({"selected_device": selected_device})
    if not isinstance(cleanup, dict):
        return _r(
            run_id,
            "cleanup",
            None,
            target,
            {"selected_device_recording": False, "recovery_required": False},
            cleanup,
            CheckStatus.ERROR,
            "cleanup state was not collected reliably",
            started,
            metadata={
                "error_code": "RECORDING_CLEANUP_UNKNOWN",
                "recovery_required": True,
                "cleanup_required": True,
            },
        )
    if (
        cleanup.get("success") is True
        and cleanup.get("complete") is True
        and cleanup.get("selected_device_recording") is False
    ):
        return _r(
            run_id,
            "cleanup",
            None,
            target,
            {"selected_device_recording": False, "recovery_required": False},
            cleanup,
            CheckStatus.PASS,
            "Recording cleanup verified",
            started,
        )
    status = CheckStatus.ERROR
    recovery = True
    message = (
        "cleanup/restoration could not be proven; recovery is required"
        if cleanup.get("reliable") is False or cleanup.get("error") or cleanup.get("unknown_state")
        else "cleanup did not complete; recovery is required"
    )
    return _r(
        run_id,
        "cleanup",
        None,
        target,
        {"selected_device_recording": False, "recovery_required": False},
        cleanup,
        status,
        message,
        started,
        metadata={"recovery_required": recovery, "cleanup_required": recovery},
    )


def _phase_error_or_fail(
    run_id: str,
    phase: str,
    target: str,
    expected: dict[str, Any],
    actual: dict[str, Any],
    started: str,
    *,
    recovery_if_unknown: bool = False,
) -> CheckResult:
    technical = actual.get("reliable") is False or actual.get("error") or actual.get("error_type")
    recovery = bool(recovery_if_unknown)
    status = CheckStatus.ERROR if technical or recovery else CheckStatus.FAIL
    message = (
        f"{phase} could not be determined reliably"
        if technical
        else f"{phase} failed; recovery is required"
        if recovery
        else f"{phase} failed"
    )
    return _r(
        run_id,
        phase,
        None,
        target,
        expected,
        actual,
        status,
        message,
        started,
        metadata={
            "error_code": (
                "RECORDING_COLLECTION_ERROR"
                if technical
                else "RECORDING_RECOVERY_REQUIRED"
                if recovery
                else None
            ),
            "recovery_required": recovery,
            "cleanup_required": recovery,
        },
    )


def _aggregate_module_status(
    results: list[CheckResult],
    recovery_required: bool,
) -> CheckStatus:
    if recovery_required:
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


def _observations(actual: dict[str, Any]) -> dict[str, Any]:
    observations = actual.get("observations")
    return observations if isinstance(observations, dict) else {}


def _expected_transition(
    phase: str,
    point: str,
    baseline_count: int | None,
    recording_config: dict[str, Any],
) -> dict[str, Any]:
    return {
        "baseline": baseline_count,
        "expected_count": _expected_count(phase, point, baseline_count, recording_config),
    }


def _expected_count(
    phase: str,
    point: str,
    baseline_count: int | None,
    recording_config: dict[str, Any],
) -> int | None:
    if baseline_count is None:
        return None
    validation = recording_config.get("validation", {})
    if phase == "after_start":
        deltas = validation.get("after_start", {})
        delta = deltas.get(START_DELTA_KEYS[point], 1) if isinstance(deltas, dict) else 1
        return baseline_count + (delta if isinstance(delta, int) else 1)
    return baseline_count


def _action_known_succeeded(actual: Any) -> bool:
    return isinstance(actual, dict) and actual.get("success") is True


def _valid_count(value: Any) -> bool:
    if not isinstance(value, dict):
        return False

    count = value.get("count")

    return (
        isinstance(count, int)
        and not isinstance(count, bool)
        and count >= 0
    )


def _count(value: Any) -> int | None:
    if not isinstance(value, dict):
        return None

    count = value.get("count")

    if (
        isinstance(count, int)
        and not isinstance(count, bool)
        and count >= 0
    ):
        return count

    return None


def _device_id(selected_device: Any) -> Any:
    if isinstance(selected_device, dict):
        return selected_device.get("id")
    return None


def _selected_device_label(actual: dict[str, Any]) -> str:
    selected_device = actual.get("selected_device")
    if isinstance(selected_device, dict):
        return str(selected_device.get("id") or selected_device.get("name") or "selected-device")
    return "selected-device"


def _site_for_point(point: str) -> str | None:
    if point.startswith("site1_"):
        return "site1"
    if point.startswith("site2_"):
        return "site2"
    return None


def _point_label(point: str) -> str:
    labels = {
        "site1_webapp": "Site 1 WebApp",
        "site2_webapp": "Site 2 WebApp",
        "site1_server": "Site 1 server",
        "site2_server": "Site 2 server",
    }
    return labels.get(point, point)


def _status(value: Any, default: CheckStatus) -> CheckStatus:
    try:
        return CheckStatus(str(value))
    except ValueError:
        return default


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
    cleaned_metadata = {k: v for k, v in (metadata or {}).items() if v is not None}
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
        metadata=cleaned_metadata,
    )
