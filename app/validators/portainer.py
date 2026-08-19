from __future__ import annotations

from typing import Any

from app.config.effective import resolve_portainer_expected
from app.domain import CheckResult, CheckStatus
from app.orchestrator.run_context import RunContext
from app.time_utils import iso_now
from app.validators.base import Validator

TASK_POLICY_STATUSES = {"WARNING", "FAIL", "ERROR", "IGNORE"}
SUPPORTED_IMAGE_COMPARISONS = {"full_reference", "repository_tag", "digest"}


class PortainerValidator(Validator):
    def validate(
        self, actual: dict[str, Any], config: dict[str, Any], context: RunContext
    ) -> list[CheckResult]:
        started = iso_now()
        results: list[CheckResult] = []
        for error in actual.get("errors") or []:
            results.append(
                _result(
                    context.run_id,
                    "collection",
                    error.get("site"),
                    error.get("site") or "portainer",
                    {"source": "Portainer Server/API", "read_only": True},
                    error,
                    CheckStatus.ERROR,
                    f"{error.get('code', 'PORTAINER_COLLECTION_ERROR')}: {error.get('message')}",
                    started,
                    metadata={"error_code": error.get("code")},
                )
            )
        expected_config = resolve_portainer_expected(config)
        expected_sites = expected_config.get("sites", {})
        actual_sites = actual.get("sites", {})
        error_sites = {error.get("site") for error in actual.get("errors") or []}
        for site_id, site_cfg in expected_sites.items():
            if site_id in error_sites:
                continue
            services = site_cfg.get("services", [])
            site_actual = actual_sites.get(site_id)
            if not isinstance(site_actual, dict):
                results.append(
                    _result(
                        context.run_id,
                        "collection",
                        site_id,
                        site_id,
                        {"site": site_id, "source": "Portainer Server/API"},
                        {"site_present": False},
                        CheckStatus.ERROR,
                        "PORTAINER_COLLECTION_ERROR: no reliable actual state for site",
                        started,
                        metadata={"error_code": "PORTAINER_COLLECTION_ERROR"},
                    )
                )
                continue
            observed_services = _services_by_name(site_actual.get("services", []))
            for service in services:
                results.extend(
                    _validate_service(
                        context.run_id,
                        site_id,
                        service,
                        observed_services.get(service.get("name")),
                        started,
                    )
                )
        return results


def _validate_service(
    run_id: str,
    site_id: str,
    service_cfg: dict[str, Any],
    observed: dict[str, Any] | None,
    started: str,
) -> list[CheckResult]:
    required = bool(service_cfg.get("required", True))
    expected = service_cfg.get("expected") or _legacy_expected(service_cfg)
    raw_name = service_cfg.get("name")
    name = str(raw_name) if raw_name is not None else "<unknown-service>"
    results: list[CheckResult] = []
    if observed is None:
        status = CheckStatus.FAIL if required else CheckStatus.SKIPPED
        results.append(
            _result(
                run_id,
                "service.exists",
                site_id,
                name,
                {"service_name": name, "required": required},
                {"service_name": name, "exists": False},
                status,
                "required service missing" if required else "optional service absent",
                started,
            )
        )
        return results
    results.append(
        _result(
            run_id,
            "service.exists",
            site_id,
            name,
            {"service_name": name, "required": required},
            {"service_name": name, "exists": True},
            CheckStatus.PASS,
            "service exists",
            started,
        )
    )
    results.append(_replica_result(run_id, site_id, name, expected, observed, "desired", started))
    results.append(_replica_result(run_id, site_id, name, expected, observed, "running", started))
    health_result = _healthy_result(run_id, site_id, name, expected, observed, started)
    if health_result is not None:
        results.append(health_result)
    image_result = _image_result(run_id, site_id, name, expected, observed, started)
    if image_result is not None:
        results.append(image_result)
    results.append(_service_state_result(run_id, site_id, name, expected, observed, started))
    results.append(_task_state_result(run_id, site_id, name, expected, observed, started))
    return results


def _replica_result(
    run_id: str,
    site_id: str,
    service_name: str,
    expected: dict[str, Any],
    observed: dict[str, Any],
    kind: str,
    started: str,
) -> CheckResult:
    expected_key = f"{kind}_replicas"
    actual_key = f"{kind}_replicas"
    expected_value = expected.get(expected_key)
    actual_value = observed.get(actual_key)
    if expected_value is None:
        return _result(
            run_id,
            f"service.{kind}_replicas",
            site_id,
            service_name,
            {"service_name": service_name, expected_key: None},
            {"service_name": service_name, actual_key: actual_value},
            CheckStatus.ERROR,
            f"PORTAINER_CONFIGURATION_ERROR: expected {kind} replicas not configured",
            started,
            metadata={"error_code": "PORTAINER_CONFIGURATION_ERROR"},
        )
    status = CheckStatus.PASS if actual_value == expected_value else CheckStatus.FAIL
    return _result(
        run_id,
        f"service.{kind}_replicas",
        site_id,
        service_name,
        {"service_name": service_name, expected_key: expected_value},
        {"service_name": service_name, actual_key: actual_value},
        status,
        f"{kind} replicas match expected"
        if status == CheckStatus.PASS
        else f"{kind} replicas mismatch",
        started,
    )


def _healthy_result(
    run_id: str,
    site_id: str,
    service_name: str,
    expected: dict[str, Any],
    observed: dict[str, Any],
    started: str,
) -> CheckResult | None:
    required = expected.get("healthy_replicas")
    if required is None:
        return None
    health = observed.get("health") or {}
    actual = observed.get("healthy_replicas")
    if actual is None or health.get("available") is False:
        return _result(
            run_id,
            "service.healthy_replicas",
            site_id,
            service_name,
            {"service_name": service_name, "healthy_replicas": required},
            {
                "service_name": service_name,
                "healthy_replicas": actual,
                "health": health,
            },
            CheckStatus.ERROR,
            "PORTAINER_INVALID_RESPONSE: health signal unavailable; cannot validate requirement",
            started,
            metadata={"error_code": "PORTAINER_INVALID_RESPONSE"},
        )
    status = CheckStatus.PASS if actual >= required else CheckStatus.FAIL
    return _result(
        run_id,
        "service.healthy_replicas",
        site_id,
        service_name,
        {"service_name": service_name, "healthy_replicas": required},
        {"service_name": service_name, "healthy_replicas": actual, "health": health},
        status,
        "healthy replicas satisfy expected state"
        if status == CheckStatus.PASS
        else "healthy replicas below expected requirement",
        started,
    )


def _image_result(
    run_id: str,
    site_id: str,
    service_name: str,
    expected: dict[str, Any],
    observed: dict[str, Any],
    started: str,
) -> CheckResult | None:
    expected_image = expected.get("image")
    if expected_image in (None, ""):
        return None
    comparison = expected.get("image_comparison", "full_reference")
    if isinstance(expected_image, dict):
        comparison = expected_image.get("comparison", comparison)
        expected_value = expected_image.get("reference") or expected_image.get("value")
    else:
        expected_value = expected_image
    actual_image = observed.get("image")
    if comparison not in SUPPORTED_IMAGE_COMPARISONS:
        return _result(
            run_id,
            "service.image",
            site_id,
            service_name,
            {"service_name": service_name, "comparison": comparison, "image": expected_value},
            {"service_name": service_name, "image": actual_image},
            CheckStatus.ERROR,
            f"PORTAINER_CONFIGURATION_ERROR: unsupported image comparison {comparison}",
            started,
            metadata={"error_code": "PORTAINER_CONFIGURATION_ERROR"},
        )
    actual_value = _image_value(actual_image, comparison)
    expected_normalized = _image_value(str(expected_value), comparison)
    status = CheckStatus.PASS if actual_value == expected_normalized else CheckStatus.FAIL
    return _result(
        run_id,
        "service.image",
        site_id,
        service_name,
        {
            "service_name": service_name,
            "comparison": comparison,
            "image": expected_normalized,
        },
        {"service_name": service_name, "comparison": comparison, "image": actual_value},
        status,
        "image matches expected" if status == CheckStatus.PASS else "image mismatch",
        started,
    )


def _service_state_result(
    run_id: str,
    site_id: str,
    service_name: str,
    expected: dict[str, Any],
    observed: dict[str, Any],
    started: str,
) -> CheckResult:
    expected_state = expected.get("service_state", "active")
    actual_state = observed.get("service_state")
    status = CheckStatus.PASS if actual_state == expected_state else CheckStatus.FAIL
    return _result(
        run_id,
        "service.state",
        site_id,
        service_name,
        {"service_name": service_name, "service_state": expected_state},
        {"service_name": service_name, "service_state": actual_state},
        status,
        "service state matches expected"
        if status == CheckStatus.PASS
        else "service state mismatch",
        started,
    )


def _task_state_result(
    run_id: str,
    site_id: str,
    service_name: str,
    expected: dict[str, Any],
    observed: dict[str, Any],
    started: str,
) -> CheckResult:
    counts = {
        "failed": int(observed.get("failed_tasks") or 0),
        "rejected": int(observed.get("rejected_tasks") or 0),
        "restarting": int(observed.get("restarting_tasks") or 0),
        "starting": int(observed.get("starting_tasks") or 0),
    }
    policy = _task_policy(expected)
    status = _task_policy_status(counts, policy)
    return _result(
        run_id,
        "service.task_state",
        site_id,
        service_name,
        {
            "service_name": service_name,
            "policy": policy,
        },
        {
            "service_name": service_name,
            "task_counts": counts,
            "task_states": observed.get("task_states", []),
        },
        status,
        "task states are healthy"
        if status == CheckStatus.PASS
        else "problematic task state detected",
        started,
    )


def _legacy_expected(service_cfg: dict[str, Any]) -> dict[str, Any]:
    expected_replicas = service_cfg.get("expected_replicas")
    return {
        "desired_replicas": expected_replicas,
        "running_replicas": expected_replicas,
        "healthy_replicas": service_cfg.get("healthy_replicas_required", expected_replicas),
        "image": service_cfg.get("expected_image"),
        "image_comparison": service_cfg.get("image_comparison", "full_reference"),
        "service_state": service_cfg.get("service_state", "active"),
        "task_state_policy": service_cfg.get("task_state_policy", {}),
    }


def _task_policy(expected: dict[str, Any]) -> dict[str, str]:
    configured = expected.get("task_state_policy") or {}
    if not isinstance(configured, dict):
        configured = {}
    policy = {
        "failed": "FAIL",
        "rejected": "FAIL",
        "restarting": "FAIL",
        "starting": "IGNORE",
    }
    for state, action in configured.items():
        if isinstance(action, str):
            policy[str(state)] = action
    return policy


def _task_policy_status(counts: dict[str, int], policy: dict[str, str]) -> CheckStatus:
    statuses: list[CheckStatus] = []
    for state, count in counts.items():
        if count <= 0:
            continue
        action = policy.get(state, "FAIL")
        if action == "IGNORE":
            continue
        if action == "<TO_VERIFY>":
            statuses.append(CheckStatus.ERROR)
            continue
        if action in {"WARNING", "FAIL", "ERROR"}:
            statuses.append(CheckStatus(action))
        else:
            statuses.append(CheckStatus.ERROR)
    if CheckStatus.ERROR in statuses:
        return CheckStatus.ERROR
    if CheckStatus.FAIL in statuses:
        return CheckStatus.FAIL
    if CheckStatus.WARNING in statuses:
        return CheckStatus.WARNING
    return CheckStatus.PASS


def _services_by_name(services: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(services, list):
        return {}
    by_name: dict[str, dict[str, Any]] = {}
    for service in services:
        if not isinstance(service, dict):
            continue
        name = service.get("name")
        if isinstance(name, str):
            by_name[name] = service
    return by_name


def _image_value(value: str | None, comparison: str) -> str | None:
    if value is None:
        return None
    if comparison == "full_reference":
        return value
    if comparison == "digest":
        return value.split("@", 1)[1] if "@" in value else None
    if comparison == "repository_tag":
        return value.split("@", 1)[0]
    return value


def _result(
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
        run_id=run_id,
        module="portainer",
        check_id=f"portainer.{check}",
        site=site,
        target=target,
        expected=expected,
        actual=actual,
        status=status,
        message=message,
        started_at=started,
        finished_at=iso_now(),
        metadata=metadata or {},
    )
