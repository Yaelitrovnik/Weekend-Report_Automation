from __future__ import annotations

from typing import Any

from app.domain import CheckResult, CheckStatus
from app.orchestrator.run_context import RunContext
from app.time_utils import iso_now
from app.validators.base import Validator

TASK_POLICY_STATUSES = {"WARNING", "FAIL", "ERROR", "IGNORE"}
DEFAULT_TASK_STATE_POLICY = {
    "failed": "FAIL",
    "rejected": "FAIL",
    "restarting": "FAIL",
    "starting": "WARNING",
}
DEFAULT_SERVICE_STATE_POLICY = {
    "running": "PASS",
    "starting": "WARNING",
    "degraded": "FAIL",
    "stopped": "FAIL",
}


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

        configured_sites = config.get("portainer_expected", {}).get("sites", {})
        actual_sites = actual.get("sites", {})
        error_sites = {error.get("site") for error in actual.get("errors") or []}
        for site_id in configured_sites:
            if site_id in error_sites:
                continue
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
            services = site_actual.get("services")
            if not isinstance(services, list):
                results.append(
                    _result(
                        context.run_id,
                        "collection",
                        site_id,
                        site_id,
                        {"services": "list"},
                        {"services": services},
                        CheckStatus.ERROR,
                        "PORTAINER_INVALID_RESPONSE: discovered services payload was not a list",
                        started,
                        metadata={"error_code": "PORTAINER_INVALID_RESPONSE"},
                    )
                )
                continue
            if not services:
                results.append(
                    _result(
                        context.run_id,
                        "discovery",
                        site_id,
                        site_id,
                        {"discovered_services": "non-empty"},
                        {"discovered_services": 0},
                        CheckStatus.ERROR,
                        "PORTAINER_INVALID_RESPONSE: no Swarm services were discovered",
                        started,
                        metadata={"error_code": "PORTAINER_INVALID_RESPONSE"},
                    )
                )
                continue
            for service in services:
                results.extend(
                    _validate_service(
                        context.run_id,
                        site_id,
                        service,
                        config,
                        started,
                    )
                )
        return results


def _validate_service(
    run_id: str,
    site_id: str,
    observed: Any,
    config: dict[str, Any],
    started: str,
) -> list[CheckResult]:
    if not isinstance(observed, dict):
        return [
            _result(
                run_id,
                "service",
                site_id,
                "malformed-service",
                {"service": "object"},
                observed,
                CheckStatus.ERROR,
                "PORTAINER_INVALID_RESPONSE: service entry was not an object",
                started,
                metadata={"error_code": "PORTAINER_INVALID_RESPONSE"},
            )
        ]

    name = str(observed.get("name") or observed.get("id") or "<unknown-service>")
    results = [
        _result(
            run_id,
            "service.exists",
            site_id,
            name,
            {"service_name": name, "discovered": True},
            {"service_name": name, "exists": True},
            CheckStatus.PASS,
            "service discovered",
            started,
        ),
        _desired_replica_result(run_id, site_id, name, observed, started),
        _running_replica_result(run_id, site_id, name, observed, started),
    ]
    health_result = _healthy_replica_result(run_id, site_id, name, observed, started)
    if health_result is not None:
        results.append(health_result)
    results.extend(
        [
            _image_result(run_id, site_id, name, observed, started),
            _service_state_result(run_id, site_id, name, observed, config, started),
            _task_state_result(run_id, site_id, name, observed, config, started),
        ]
    )
    return results


def _desired_replica_result(
    run_id: str,
    site_id: str,
    service_name: str,
    observed: dict[str, Any],
    started: str,
) -> CheckResult:
    desired = _replica_count(observed.get("desired_replicas"))
    if desired is None:
        return _result(
            run_id,
            "service.desired_replicas",
            site_id,
            service_name,
            {"desired_replicas": "available"},
            {"service_name": service_name, "desired_replicas": observed.get("desired_replicas")},
            CheckStatus.ERROR,
            "PORTAINER_INVALID_RESPONSE: reliable desired replica count unavailable",
            started,
            metadata={"error_code": "PORTAINER_INVALID_RESPONSE"},
        )
    return _result(
        run_id,
        "service.desired_replicas",
        site_id,
        service_name,
        {"desired_replicas": "collected_actual_value"},
        {"service_name": service_name, "desired_replicas": desired},
        CheckStatus.PASS,
        "desired replica count collected",
        started,
    )


def _running_replica_result(
    run_id: str,
    site_id: str,
    service_name: str,
    observed: dict[str, Any],
    started: str,
) -> CheckResult:
    desired = _replica_count(observed.get("desired_replicas"))
    running = _replica_count(observed.get("running_replicas"))
    if desired is None or running is None:
        return _result(
            run_id,
            "service.running_replicas",
            site_id,
            service_name,
            {"running_replicas": "must_equal_desired_replicas"},
            {
                "service_name": service_name,
                "desired_replicas": observed.get("desired_replicas"),
                "running_replicas": observed.get("running_replicas"),
            },
            CheckStatus.ERROR,
            "PORTAINER_INVALID_RESPONSE: reliable replica counts unavailable",
            started,
            metadata={"error_code": "PORTAINER_INVALID_RESPONSE"},
        )
    status = CheckStatus.PASS if running == desired else CheckStatus.FAIL
    return _result(
        run_id,
        "service.running_replicas",
        site_id,
        service_name,
        {"service_name": service_name, "running_replicas": desired},
        {"service_name": service_name, "desired_replicas": desired, "running_replicas": running},
        status,
        "running replicas equal desired replicas"
        if status == CheckStatus.PASS
        else "running replicas do not equal desired replicas",
        started,
    )


def _healthy_replica_result(
    run_id: str,
    site_id: str,
    service_name: str,
    observed: dict[str, Any],
    started: str,
) -> CheckResult | None:
    health = observed.get("health") or {}
    if not isinstance(health, dict) or health.get("available") is not True:
        return None
    desired = _replica_count(observed.get("desired_replicas"))
    healthy = _replica_count(observed.get("healthy_replicas"))
    if desired is None or healthy is None:
        return _result(
            run_id,
            "service.healthy_replicas",
            site_id,
            service_name,
            {"healthy_replicas": "must_equal_desired_replicas_when_available"},
            {
                "service_name": service_name,
                "desired_replicas": observed.get("desired_replicas"),
                "healthy_replicas": observed.get("healthy_replicas"),
                "health": health,
            },
            CheckStatus.ERROR,
            "PORTAINER_INVALID_RESPONSE: reliable healthy replica count unavailable",
            started,
            metadata={"error_code": "PORTAINER_INVALID_RESPONSE"},
        )
    status = CheckStatus.PASS if healthy == desired else CheckStatus.FAIL
    return _result(
        run_id,
        "service.healthy_replicas",
        site_id,
        service_name,
        {"service_name": service_name, "healthy_replicas": desired},
        {
            "service_name": service_name,
            "desired_replicas": desired,
            "healthy_replicas": healthy,
            "health": health,
        },
        status,
        "healthy replicas equal desired replicas"
        if status == CheckStatus.PASS
        else "healthy replicas do not equal desired replicas",
        started,
    )


def _image_result(
    run_id: str,
    site_id: str,
    service_name: str,
    observed: dict[str, Any],
    started: str,
) -> CheckResult:
    image = observed.get("image")
    if not isinstance(image, str) or not image.strip():
        return _result(
            run_id,
            "service.image",
            site_id,
            service_name,
            {"image": "available_for_cross_site_parity"},
            {"service_name": service_name, "image": image},
            CheckStatus.ERROR,
            "PORTAINER_INVALID_RESPONSE: service image reference unavailable",
            started,
            metadata={"error_code": "PORTAINER_INVALID_RESPONSE"},
        )
    return _result(
        run_id,
        "service.image",
        site_id,
        service_name,
        {"image": "collected_actual_value"},
        {"service_name": service_name, "image": image.strip()},
        CheckStatus.PASS,
        "service image collected for cross-site parity",
        started,
    )


def _service_state_result(
    run_id: str,
    site_id: str,
    service_name: str,
    observed: dict[str, Any],
    config: dict[str, Any],
    started: str,
) -> CheckResult:
    actual_state = observed.get("service_state")
    if not isinstance(actual_state, str) or not actual_state:
        return _result(
            run_id,
            "service.state",
            site_id,
            service_name,
            {"service_state": "available"},
            {"service_name": service_name, "service_state": actual_state},
            CheckStatus.ERROR,
            "PORTAINER_INVALID_RESPONSE: service state unavailable",
            started,
            metadata={"error_code": "PORTAINER_INVALID_RESPONSE"},
        )
    policy = _service_state_policy(config)
    try:
        status = CheckStatus(policy.get(actual_state, "FAIL"))
    except ValueError:
        status = CheckStatus.ERROR
    return _result(
        run_id,
        "service.state",
        site_id,
        service_name,
        {"service_name": service_name, "policy": policy},
        {"service_name": service_name, "service_state": actual_state},
        status,
        "service state is healthy"
        if status == CheckStatus.PASS
        else "service state requires attention",
        started,
    )


def _task_state_result(
    run_id: str,
    site_id: str,
    service_name: str,
    observed: dict[str, Any],
    config: dict[str, Any],
    started: str,
) -> CheckResult:
    counts = _task_counts(observed)
    policy = _task_policy(config)
    if counts is None:
        return _result(
            run_id,
            "service.task_state",
            site_id,
            service_name,
            {"service_name": service_name, "policy": policy},
            {
                "service_name": service_name,
                "task_counts": {
                    "failed": observed.get("failed_tasks"),
                    "rejected": observed.get("rejected_tasks"),
                    "restarting": observed.get("restarting_tasks"),
                    "starting": observed.get("starting_tasks"),
                },
                "task_states": observed.get("task_states", []),
            },
            CheckStatus.ERROR,
            "PORTAINER_INVALID_RESPONSE: task-state counts are malformed",
            started,
            metadata={"error_code": "PORTAINER_INVALID_RESPONSE"},
        )
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


def _replica_count(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _task_counts(
    observed: dict[str, Any],
) -> dict[str, int] | None:
    counts: dict[str, int] = {}
    for state, key in {
        "failed": "failed_tasks",
        "rejected": "rejected_tasks",
        "restarting": "restarting_tasks",
        "starting": "starting_tasks",
    }.items():
        if key not in observed:
            return None
        value = observed[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        counts[state] = value
    return counts


def _task_policy(config: dict[str, Any]) -> dict[str, str]:
    configured = config.get("rules", {}).get("portainer", {}).get("task_state_policy") or {}
    policy = DEFAULT_TASK_STATE_POLICY.copy()
    if isinstance(configured, dict):
        for state, action in configured.items():
            if isinstance(action, str):
                policy[str(state)] = action
    return policy


def _service_state_policy(config: dict[str, Any]) -> dict[str, str]:
    configured = config.get("rules", {}).get("portainer", {}).get("service_state_policy") or {}
    policy = DEFAULT_SERVICE_STATE_POLICY.copy()
    if isinstance(configured, dict):
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
        if action in TASK_POLICY_STATUSES:
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
