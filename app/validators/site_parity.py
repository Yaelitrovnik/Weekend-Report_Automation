from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.domain import CheckResult, CheckStatus
from app.orchestrator.run_context import RunContext
from app.time_utils import iso_now

FIELD_CHECK_IDS = {
    "service_presence": "portainer.service.exists",
    "desired_replicas": "portainer.service.desired_replicas",
    "running_replicas": "portainer.service.running_replicas",
    "healthy_replicas": "portainer.service.healthy_replicas",
    "image": "portainer.service.image",
    "service_state": "portainer.service.state",
}


class SiteParityValidator:
    def validate(
        self, actual: dict[str, Any], config: dict[str, Any], context: RunContext
    ) -> list[CheckResult]:
        started = iso_now()
        parity = config.get("rules", {}).get("parity", [])
        results: list[CheckResult] = []
        observed_results: list[CheckResult] = actual.get("results", [])
        by_module_site: dict[tuple[str, str], list[CheckResult]] = defaultdict(list)
        for result in observed_results:
            if result.site:
                by_module_site[(result.module, result.site)].append(result)
        for rule in parity:
            if not rule.get("enabled", False):
                continue
            module = rule["module"]
            fields = rule.get("fields") or [rule["field"]]
            site1, site2 = rule["sites"]
            for field in fields:
                value1 = _extract(by_module_site.get((module, site1), []), field)
                value2 = _extract(by_module_site.get((module, site2), []), field)
                match = value1 == value2
                allowed = (
                    False
                    if match
                    else _allowed_difference(rule, field, site1, site2, value1, value2)
                )
                status = (
                    CheckStatus.PASS
                    if match or allowed
                    else CheckStatus(rule.get("mismatch_status", "WARNING"))
                )
                message = (
                    "parity match after independent expected-state validation"
                    if match
                    else "explicitly allowed parity difference"
                    if allowed
                    else "parity mismatch after independent expected-state validation"
                )
                results.append(
                    CheckResult(
                        context.run_id,
                        "site_parity",
                        f"parity.{module}.{field}",
                        status,
                        message,
                        site=None,
                        target=f"{module}.{field}",
                        expected={site1: value1, site2: value2},
                        actual={
                            "match": match,
                            "allowed_difference": allowed,
                            "field": field,
                            "sites": [site1, site2],
                        },
                        started_at=started,
                        finished_at=iso_now(),
                        metadata={
                            "parity_only": True,
                            "module": module,
                            "field": field,
                            "sites": [site1, site2],
                            "allowed_difference": allowed,
                        },
                    )
                )
        return results


def _extract(results: list[CheckResult], field: str) -> dict[str, Any]:
    values: dict[str, Any] = {}
    check_id = FIELD_CHECK_IDS.get(field)
    for result in results:
        if check_id and result.check_id != check_id:
            continue
        target = result.target or result.check_id
        value = _field_value(result, field)
        if value is not None:
            values[str(target)] = value
    if values:
        return dict(sorted(values.items()))
    fallback = []
    for result in results:
        value = _field_value(result, field)
        if value is not None:
            fallback.append(value)
    return {"values": sorted(fallback, key=lambda item: str(item))}


def _field_value(result: CheckResult, field: str) -> Any:
    if field == "service_presence" and isinstance(result.actual, dict):
        return result.actual.get("exists")
    if isinstance(result.actual, dict) and field in result.actual:
        return result.actual[field]
    if isinstance(result.expected, dict) and field in result.expected:
        return result.expected[field]
    return None


def _allowed_difference(
    rule: dict[str, Any],
    field: str,
    site1: str,
    site2: str,
    value1: dict[str, Any],
    value2: dict[str, Any],
) -> bool:
    for item in rule.get("allowed_differences", []):
        if not isinstance(item, dict):
            continue
        if item.get("field", field) != field:
            continue
        target = item.get("target")
        if target:
            if value1.get(target) == item.get("site_values", {}).get(site1) and value2.get(
                target
            ) == item.get("site_values", {}).get(site2):
                return True
            continue
        site_values = item.get("site_values")
        if (
            isinstance(site_values, dict)
            and value1 == site_values.get(site1)
            and value2 == site_values.get(site2)
        ):
            return True
    return False
