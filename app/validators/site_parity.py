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

        sites = _runtime_site_ids(config)
        if len(sites) == 1:
            return []

        for rule in parity:
            if not rule.get("enabled", False):
                continue

            module = rule.get("module")
            fields = rule.get("fields") or [rule.get("field")]

            if (
                not isinstance(module, str)
                or "sites" in rule
                or not sites
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
                            "minimum_site_count": 1,
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

            for field in [field for field in fields if isinstance(field, str) and field]:
                extracted = {
                    site: _extract(by_module_site.get((module, site), []), module, field)
                    for site in sites
                }
                values = {site: value for site, (value, _) in extracted.items()}
                missing_sites = [
                    site for site, (_, available) in extracted.items() if not available
                ]

                if missing_sites:
                    results.append(
                        CheckResult(
                            context.run_id,
                            "site_parity",
                            f"parity.{module}.{field}",
                            CheckStatus.ERROR,
                            "parity comparison unavailable because reliable site data is missing",
                            site=None,
                            target=f"{module}.{field}",
                            expected={"sites": sites, "field": field},
                            actual={
                                "match": None,
                                "comparable": False,
                                "missing_sites": missing_sites,
                                "values": values,
                            },
                            started_at=started,
                            finished_at=iso_now(),
                            metadata={
                                "parity_only": True,
                                "module": module,
                                "field": field,
                                "sites": sites,
                                "error_code": "PARITY_DATA_UNAVAILABLE",
                            },
                        )
                    )
                    continue

                match = all(value == values[sites[0]] for value in values.values())
                allowed = (
                    False
                    if match
                    else _allowed_difference(rule, field, values)
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
                        expected=values,
                        actual={
                            "match": match,
                            "values": values,
                            "mismatches": _mismatches(values),
                            "comparable": True,
                            "allowed_difference": allowed,
                            "field": field,
                            "sites": sites,
                        },
                        started_at=started,
                        finished_at=iso_now(),
                        metadata={
                            "parity_only": True,
                            "module": module,
                            "field": field,
                            "sites": sites,
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
    when collection failed on any site.
    """

    check_id = FIELD_CHECK_IDS.get(module, {}).get(field)
    if check_id is None:
        return {}, False

    values: dict[str, Any] = {}
    reliable = True
    for result in results:
        if result.status == CheckStatus.ERROR and result.check_id in {
            f"{module}.collection", f"{module}.discovery", f"{module}.service",
        }:
            reliable = False
        if result.check_id != check_id:
            continue
        if result.status == CheckStatus.ERROR:
            reliable = False
            continue

        target = result.target or result.check_id
        value = _field_value(result, field)
        if value is not None:
            values[str(target)] = value
        else:
            reliable = False

    return dict(sorted(values.items())), reliable and bool(values)


def _field_value(result: CheckResult, field: str) -> Any:
    if not isinstance(result.actual, dict):
        return None
    if field == "service_presence":
        return result.actual.get("exists")
    return result.actual.get(field)


def _mismatches(values: dict[str, dict[str, Any]]) -> dict[str, Any]:
    targets = sorted({target for value in values.values() for target in value})
    mismatches = {}
    for target in targets:
        observed = {
            site: {"present": target in value, "value": value.get(target)}
            for site, value in values.items()
        }
        first = next(iter(observed.values()))
        if any(value != first for value in observed.values()):
            mismatches[target] = observed
    return mismatches


def _allowed_difference(
    rule: dict[str, Any],
    field: str,
    values: dict[str, dict[str, Any]],
) -> bool:
    allowed_targets: set[str] = set()
    for item in rule.get("allowed_differences", []):
        if not isinstance(item, dict) or item.get("field", field) != field:
            continue
        site_values = item.get("site_values")
        if not isinstance(site_values, dict) or set(site_values) != set(values):
            continue
        target = item.get("target")
        if target:
            # An exception for one service cannot excuse differences in another.
            # null explicitly permits an absent target on a particular site.
            if all(value.get(target) == site_values[site] for site, value in values.items()):
                allowed_targets.add(target)
        elif all(value == site_values[site] for site, value in values.items()):
            return True
    return set(_mismatches(values)).issubset(allowed_targets)
