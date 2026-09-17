from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.domain import CheckResult, CheckStatus
from app.orchestrator.run_context import RunContext
from app.time_utils import iso_now

FIELD_CHECK_IDS = {
    "portainer": {
        "service_presence": "portainer.service.exists",
        "desired_replicas": "portainer.service.desired_replicas",
        "running_replicas": "portainer.service.running_replicas",
        "healthy_replicas": "portainer.service.healthy_replicas",
        "image": "portainer.service.image",
        "service_state": "portainer.service.state",
    },
    "doctor": {
        "service_presence": "doctor.service.exists",
    },
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

            module = rule.get("module")
            fields = rule.get("fields") or [rule.get("field")]
            sites = _runtime_site_ids(config)

            if (
                not isinstance(module, str)
                or "sites" in rule
                or len(sites) != 2
            ):
                results.append(
                    CheckResult(
                        context.run_id,
                        "site_parity",
                        "parity.configuration",
                        CheckStatus.ERROR,
                        "invalid parity rule or runtime site configuration",
                        site=None,
                        target=str(module or "unknown"),
                        expected={
                            "site_source": "runtime SITE_COUNT/SITE*_ID",
                            "site_count": 2,
                        },
                        actual={
                            "comparable": False,
                            "runtime_sites": sites,
                            "rules_contains_sites": "sites" in rule,
                        },
                        started_at=started,
                        finished_at=iso_now(),
                        metadata={
                            "parity_only": True,
                            "error_code": "PARITY_CONFIGURATION_ERROR",
                        },
                    )
                )
                continue

            if not any(result.module == module for result in observed_results):
                continue

            site1, site2 = sites

            for field in [field for field in fields if isinstance(field, str) and field]:
                value1, available1 = _extract(
                    by_module_site.get((module, site1), []),
                    module,
                    field,
                )
                value2, available2 = _extract(
                    by_module_site.get((module, site2), []),
                    module,
                    field,
                )

                if not available1 or not available2:
                    missing_sites = [
                        site
                        for site, available in [(site1, available1), (site2, available2)]
                        if not available
                    ]
                    results.append(
                        CheckResult(
                            context.run_id,
                            "site_parity",
                            f"parity.{module}.{field}",
                            CheckStatus.ERROR,
                            "parity comparison unavailable because reliable site data is missing",
                            site=None,
                            target=f"{module}.{field}",
                            expected={"sites": [site1, site2], "field": field},
                            actual={
                                "match": None,
                                "comparable": False,
                                "missing_sites": missing_sites,
                                "values": {site1: value1, site2: value2},
                            },
                            started_at=started,
                            finished_at=iso_now(),
                            metadata={
                                "parity_only": True,
                                "module": module,
                                "field": field,
                                "sites": [site1, site2],
                                "error_code": "PARITY_DATA_UNAVAILABLE",
                            },
                        )
                    )
                    continue

                match = value1 == value2
                allowed = (
                    False
                    if match
                    else _allowed_difference(rule, field, site1, site2, value1, value2)
                )

                if match or allowed:
                    status = CheckStatus.PASS
                else:
                    try:
                        status = CheckStatus(rule.get("mismatch_status", "WARNING"))
                    except ValueError:
                        status = CheckStatus.ERROR

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
                            "comparable": True,
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


def _runtime_site_ids(config: dict[str, Any]) -> list[str]:
    configured = config.get("sites", {}).get("sites", [])
    if not isinstance(configured, list):
        return []

    site_ids: list[str] = []
    for site in configured:
        if not isinstance(site, dict):
            return []
        site_id = site.get("id")
        if not isinstance(site_id, str) or not site_id.strip():
            return []
        site_ids.append(site_id.strip())

    if len(site_ids) != len(set(site_ids)):
        return []
    return site_ids


def _extract(
    results: list[CheckResult],
    module: str,
    field: str,
) -> tuple[dict[str, Any], bool]:
    """Extract reliable actual values for one configured parity field.

    Parity must compare observed state only. It must never fall back to
    configured expected values, because that can create a false parity PASS
    when collection failed on both sites.
    """

    check_id = FIELD_CHECK_IDS.get(module, {}).get(field)
    if check_id is None:
        return {}, False

    values: dict[str, Any] = {}
    for result in results:
        if result.check_id != check_id:
            continue
        if result.status == CheckStatus.ERROR:
            continue

        target = result.target or result.check_id
        value = _field_value(result, field)
        if value is not None:
            values[str(target)] = value

    return dict(sorted(values.items())), bool(values)


def _field_value(result: CheckResult, field: str) -> Any:
    if not isinstance(result.actual, dict):
        return None
    if field == "service_presence":
        return result.actual.get("exists")
    return result.actual.get(field)


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
