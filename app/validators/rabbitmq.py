from __future__ import annotations

from typing import Any

from app.config.effective import resolve_rabbitmq_expected
from app.domain import CheckResult, CheckStatus
from app.orchestrator.run_context import RunContext
from app.time_utils import iso_now
from app.validators.base import Validator
from app.validators.engine import threshold_status


class RabbitMQValidator(Validator):
    def validate(
        self, actual: dict[str, Any], config: dict[str, Any], context: RunContext
    ) -> list[CheckResult]:
        started = iso_now()
        results: list[CheckResult] = []
        if actual.get("error"):
            results.append(
                _r(
                    context.run_id,
                    "collection",
                    None,
                    "rabbitmq",
                    {"source": "RabbitMQ Management API", "read_only": True},
                    actual,
                    CheckStatus.ERROR,
                    actual["error"],
                    started,
                )
            )
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
        expected_sites = resolve_rabbitmq_expected(config).get("sites", {})
        actual_sites = actual.get("sites", {})
        for site, expected in expected_sites.items():
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
            results.extend(_validate_site(context.run_id, site, expected, observed, started))
        return results


def _validate_site(
    run_id: str,
    site: str,
    expected: dict[str, Any],
    observed: dict[str, Any],
    started: str,
) -> list[CheckResult]:
    results: list[CheckResult] = []
    vhosts = {v.get("name") for v in observed.get("vhosts", []) if isinstance(v, dict)}
    for vhost in expected.get("vhosts", []):
        if not isinstance(vhost, dict):
            continue
        name = str(vhost.get("name"))
        exists = name in vhosts
        results.append(
            _existence_result(
                run_id,
                "vhost.exists",
                site,
                name,
                vhost,
                exists,
                started,
                missing_message="required vhost missing",
                present_message="vhost exists",
            )
        )

    queues = {
        (q.get("vhost"), q.get("name")): q
        for q in observed.get("queues", [])
        if isinstance(q, dict)
    }
    for queue in expected.get("queues", []):
        if not isinstance(queue, dict):
            continue
        name = str(queue.get("name"))
        q = queues.get((queue.get("vhost"), queue.get("name")))
        if q is None:
            results.append(
                _existence_result(
                    run_id,
                    "queue.exists",
                    site,
                    name,
                    queue,
                    False,
                    started,
                    missing_message="required queue missing",
                    present_message="queue exists",
                )
            )
            continue
        results.append(
            _existence_result(
                run_id,
                "queue.exists",
                site,
                name,
                queue,
                True,
                started,
                missing_message="required queue missing",
                present_message="queue exists",
            )
        )
        for prop in ["durable", "auto_delete", "exclusive"]:
            if prop in queue:
                status = CheckStatus.PASS if q.get(prop) == queue[prop] else CheckStatus.FAIL
                results.append(
                    _r(
                        run_id,
                        f"queue.{prop}",
                        site,
                        name,
                        queue[prop],
                        q.get(prop),
                        status,
                        f"queue {prop} "
                        f"{'matches' if status == CheckStatus.PASS else 'mismatch'}",
                        started,
                    )
                )
        min_consumers = int(queue.get("min_consumers", 0))
        consumers = int(q.get("consumers", 0))
        status = CheckStatus.PASS if consumers >= min_consumers else CheckStatus.FAIL
        results.append(
            _r(
                run_id,
                "queue.consumers",
                site,
                name,
                min_consumers,
                consumers,
                status,
                "consumer count satisfies minimum"
                if status == CheckStatus.PASS
                else "consumers below minimum",
                started,
            )
        )
        metric = queue.get("backlog_metric", "messages")
        messages = _numeric(q.get(metric, q.get("messages", 0)))
        warning_messages = _numeric(queue.get("warning_messages", 0))
        critical_messages = _numeric(queue.get("critical_messages", warning_messages))
        status = threshold_status(
            messages,
            warning_messages,
            critical_messages,
        )
        results.append(
            _r(
                run_id,
                "queue.backlog",
                site,
                name,
                {
                    "metric": metric,
                    "warning": warning_messages,
                    "critical": critical_messages,
                },
                {metric: messages},
                status,
                f"{metric}={messages}",
                started,
            )
        )

    exchanges = {
        (e.get("vhost"), e.get("name")): e
        for e in observed.get("exchanges", [])
        if isinstance(e, dict)
    }
    for exchange in expected.get("exchanges", []):
        if not isinstance(exchange, dict):
            continue
        name = str(exchange.get("name"))
        e = exchanges.get((exchange.get("vhost"), exchange.get("name")))
        if e is None:
            results.append(
                _existence_result(
                    run_id,
                    "exchange.exists",
                    site,
                    name,
                    exchange,
                    False,
                    started,
                    missing_message="required exchange missing",
                    present_message="exchange exists",
                )
            )
            continue
        results.append(
            _existence_result(
                run_id,
                "exchange.exists",
                site,
                name,
                exchange,
                True,
                started,
                missing_message="required exchange missing",
                present_message="exchange exists",
            )
        )
        for prop in ["type", "durable", "auto_delete"]:
            status = CheckStatus.PASS if e.get(prop) == exchange[prop] else CheckStatus.FAIL
            results.append(
                _r(
                    run_id,
                    f"exchange.{prop}",
                    site,
                    name,
                    exchange[prop],
                    e.get(prop),
                    status,
                    f"exchange {prop} "
                    f"{'matches' if status == CheckStatus.PASS else 'mismatch'}",
                    started,
                )
            )

    binding_set = {
        (
            b.get("vhost"),
            b.get("source"),
            b.get("destination_type"),
            b.get("destination"),
            b.get("routing_key"),
        )
        for b in observed.get("bindings", [])
        if isinstance(b, dict)
    }
    for binding in expected.get("bindings", []):
        if not isinstance(binding, dict):
            continue
        binding_key = (
            binding.get("vhost"),
            binding.get("source"),
            binding.get("destination_type"),
            binding.get("destination"),
            binding.get("routing_key"),
        )
        exists = binding_key in binding_set
        results.append(
            _existence_result(
                run_id,
                "binding.exists",
                site,
                str(binding.get("destination")),
                binding,
                exists,
                started,
                missing_message="required binding missing",
                present_message="binding exists",
            )
        )

    for node in observed.get("nodes", []):
        if not isinstance(node, dict):
            continue
        if node.get("mem_alarm"):
            results.append(
                _r(
                    run_id,
                    "node.memory_alarm",
                    site,
                    node.get("name", "node"),
                    {"alarm": False},
                    {"alarm": True},
                    CheckStatus.FAIL,
                    "memory alarm active",
                    started,
                )
            )
        if node.get("disk_free_alarm"):
            results.append(
                _r(
                    run_id,
                    "node.disk_alarm",
                    site,
                    node.get("name", "node"),
                    {"alarm": False},
                    {"alarm": True},
                    CheckStatus.FAIL,
                    "disk alarm active",
                    started,
                )
            )
    return results


def _numeric(value: Any) -> float:
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        return float(value)
    return 0.0


def _existence_result(
    run_id: str,
    check: str,
    site: str,
    target: str,
    expected: dict[str, Any],
    exists: bool,
    started: str,
    *,
    missing_message: str,
    present_message: str,
) -> CheckResult:
    required = bool(expected.get("required", True))
    status = CheckStatus.PASS if exists else CheckStatus.FAIL if required else CheckStatus.SKIPPED
    return _r(
        run_id,
        check,
        site,
        target,
        expected,
        {"exists": exists, "required": required},
        status,
        present_message if exists else missing_message if required else "optional topology absent",
        started,
    )


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
        metadata=metadata or {},
    )
