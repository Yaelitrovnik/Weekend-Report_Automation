from __future__ import annotations

from typing import Any

from app.config.effective import resolve_rabbitmq_expected
from app.domain import CheckResult, CheckStatus
from app.orchestrator.run_context import RunContext
from app.time_utils import iso_now
from app.validators.base import Validator

QUEUE_FIELDS = ("ready", "unacked", "total")
NODE_RESOURCE_FIELDS = (
    "file_descriptors",
    "socket_descriptors",
    "erlang_processes",
    "disk_space",
)


class RabbitMQValidator(Validator):
    def validate(
        self, actual: dict[str, Any], config: dict[str, Any], context: RunContext
    ) -> list[CheckResult]:
        started = iso_now()
        results: list[CheckResult] = []
        for error in actual.get("errors") or []:
            results.append(
                _r(
                    context.run_id,
                    "collection",
                    error.get("site"),
                    error.get("site") or "rabbitmq",
                    {"source": "RabbitMQ Management API", "read_only": True},
                    error,
                    CheckStatus.ERROR,
                    f"{error.get('code', 'RABBITMQ_COLLECTION_ERROR')}: {error.get('message')}",
                    started,
                    metadata={"error_code": error.get("code")},
                )
            )

        expected_config = resolve_rabbitmq_expected(config)
        expected_sites = expected_config.get("sites", {})
        actual_sites = actual.get("sites", {})
        collection_error_sites = {error.get("site") for error in actual.get("errors") or []}

        for site in expected_sites:
            if site in collection_error_sites:
                continue
            observed = actual_sites.get(site)
            if not isinstance(observed, dict):
                results.append(
                    _r(
                        context.run_id,
                        "collection",
                        site,
                        site,
                        {"site": site, "source": "RabbitMQ Management API"},
                        {"site_present": False},
                        CheckStatus.ERROR,
                        "RABBITMQ_COLLECTION_ERROR: no reliable actual state for site",
                        started,
                        metadata={"error_code": "RABBITMQ_COLLECTION_ERROR"},
                    )
                )
                continue
            results.extend(_validate_site(context.run_id, site, expected_config, observed, started))

        return results


def _validate_site(
    run_id: str,
    site: str,
    expected_config: dict[str, Any],
    observed: dict[str, Any],
    started: str,
) -> list[CheckResult]:
    results: list[CheckResult] = []
    expected_queues = (expected_config.get("queues") or {}).get("expected", {})
    queue_error_status = _status(
        (expected_config.get("queues") or {}).get("nonzero_after_rechecks_status"),
        CheckStatus.ERROR,
    )
    for queue in observed.get("queues", []):
        if not isinstance(queue, dict):
            results.append(
                _r(
                    run_id,
                    "queue.counts",
                    site,
                    "malformed-queue",
                    expected_queues,
                    queue,
                    CheckStatus.ERROR,
                    "RABBITMQ_INVALID_RESPONSE: queue entry was not an object",
                    started,
                    metadata={"error_code": "RABBITMQ_INVALID_RESPONSE"},
                )
            )
            continue
        results.append(
            _queue_result(run_id, site, expected_queues, queue, queue_error_status, started)
        )

    expected_nodes = (expected_config.get("nodes") or {}).get("expected", {})
    node_error_status = _status(
        (expected_config.get("nodes") or {}).get("unhealthy_status"),
        CheckStatus.ERROR,
    )
    for node in observed.get("nodes", []):
        if not isinstance(node, dict):
            results.append(
                _r(
                    run_id,
                    "node.resources",
                    site,
                    "malformed-node",
                    expected_nodes,
                    node,
                    CheckStatus.ERROR,
                    "RABBITMQ_INVALID_RESPONSE: node entry was not an object",
                    started,
                    metadata={"error_code": "RABBITMQ_INVALID_RESPONSE"},
                )
            )
            continue
        results.extend(
            _node_results(run_id, site, expected_nodes, node, node_error_status, started)
        )

    return results


def _queue_result(
    run_id: str,
    site: str,
    expected: dict[str, Any],
    queue: dict[str, Any],
    nonzero_status: CheckStatus,
    started: str,
) -> CheckResult:
    target = _queue_target(queue)
    counts = {field: _integer_or_none(queue.get(field)) for field in QUEUE_FIELDS}
    if any(value is None for value in counts.values()):
        return _r(
            run_id,
            "queue.counts",
            site,
            target,
            expected,
            queue,
            CheckStatus.ERROR,
            "RABBITMQ_INVALID_RESPONSE: reliable queue counts were unavailable",
            started,
            metadata={"error_code": "RABBITMQ_INVALID_RESPONSE"},
        )
    mismatches = {
        field: {"expected": expected.get(field, 0), "actual": counts[field]}
        for field in QUEUE_FIELDS
        if counts[field] != expected.get(field, 0)
    }
    status = CheckStatus.PASS if not mismatches else nonzero_status
    checks_performed = _checks_performed(queue)
    return _r(
        run_id,
        "queue.counts",
        site,
        target,
        {
            "ready": expected.get("ready", 0),
            "unacked": expected.get("unacked", 0),
            "total": expected.get("total", 0),
        },
        {
            "site": site,
            "queue": queue.get("name"),
            "vhost": queue.get("vhost"),
            "ready": counts["ready"],
            "unacked": counts["unacked"],
            "total": counts["total"],
            "checks_performed": checks_performed,
            "snapshots": queue.get("snapshots", []),
        },
        status,
        "queue counts are all zero"
        if status == CheckStatus.PASS
        else "queue counts remained non-zero after configured rechecks",
        started,
        metadata={"checks_performed": checks_performed},
    )


def _node_results(
    run_id: str,
    site: str,
    expected: dict[str, Any],
    node: dict[str, Any],
    unhealthy_status: CheckStatus,
    started: str,
) -> list[CheckResult]:
    target = str(node.get("name") or node.get("node") or "node")
    states = node.get("resource_states")
    raw_metrics = node.get("raw_resource_metrics", {})
    results: list[CheckResult] = []

    if not isinstance(states, dict):
        states = {}

    for field in NODE_RESOURCE_FIELDS:
        actual_state = states.get(field)
        expected_state = expected.get(field, "green")

        if isinstance(actual_state, str):
            actual_state = actual_state.strip().lower()
        if isinstance(expected_state, str):
            expected_state = expected_state.strip().lower()

        if actual_state == expected_state:
            status = CheckStatus.PASS
            message = f"{_label(field)} is green"
        elif isinstance(actual_state, str) and actual_state:
            status = unhealthy_status
            message = f"{_label(field)} is unhealthy"
        else:
            status = CheckStatus.ERROR
            message = (
                f"RABBITMQ_NODE_HEALTH_MAPPING_UNRESOLVED: {_label(field)} green-state "
                "mapping is not available from the collected API fields"
            )

        results.append(
            _r(
                run_id,
                f"node.{field}",
                site,
                target,
                {field: expected_state},
                {
                    field: actual_state,
                    "node": target,
                    "raw_resource_metrics": raw_metrics,
                },
                status,
                message,
                started,
                metadata={
                    "error_code": "RABBITMQ_NODE_HEALTH_MAPPING_UNRESOLVED"
                    if status == CheckStatus.ERROR
                    else None
                },
            )
        )

    return results


def _queue_target(queue: dict[str, Any]) -> str:
    name = str(queue.get("name") or "<unknown-queue>")
    vhost = queue.get("vhost")
    if isinstance(vhost, str) and vhost:
        return f"{vhost}/{name}"
    return name


def _checks_performed(queue: dict[str, Any]) -> int:
    checks = queue.get("checks_performed")
    if isinstance(checks, int) and not isinstance(checks, bool) and checks > 0:
        return checks
    snapshots = queue.get("snapshots")
    if isinstance(snapshots, list) and snapshots:
        return len(snapshots)
    return 1


def _integer_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _status(value: Any, default: CheckStatus) -> CheckStatus:
    try:
        return CheckStatus(str(value))
    except ValueError:
        return default


def _label(value: str) -> str:
    return value.replace("_", " ").title()


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
        "rabbitmq",
        f"rabbitmq.{check}",
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
