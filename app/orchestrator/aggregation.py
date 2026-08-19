from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.domain import (
    STATUS_STRENGTH,
    CheckResult,
    CheckStatus,
    ModuleSummary,
    SiteSummary,
)

DEFAULT_POLICY = {
    "fail_blocks": True,
    "error_blocks": True,
    "manual_review_blocks": True,
    "skipped_blocks": True,
    "warning_overall_status": CheckStatus.WARNING.value,
}


def aggregate_status(
    results: list[CheckResult],
    config: dict[str, Any] | None = None,
) -> CheckStatus:
    if config is None:
        return _aggregate_by_strength(results)
    if not results:
        return CheckStatus.SKIPPED
    policy = _aggregation_policy(config)
    statuses = {result.status for result in results}
    if CheckStatus.ERROR in statuses and policy["error_blocks"]:
        return CheckStatus.ERROR
    if CheckStatus.FAIL in statuses and policy["fail_blocks"]:
        return CheckStatus.FAIL
    if CheckStatus.MANUAL_REVIEW in statuses and policy["manual_review_blocks"]:
        return CheckStatus.MANUAL_REVIEW
    if CheckStatus.SKIPPED in statuses and policy["skipped_blocks"]:
        return CheckStatus.SKIPPED
    if CheckStatus.WARNING in statuses:
        return _warning_status(policy)
    nonblocking_nonpass = statuses & {
        CheckStatus.ERROR,
        CheckStatus.FAIL,
        CheckStatus.MANUAL_REVIEW,
        CheckStatus.SKIPPED,
    }
    if nonblocking_nonpass:
        fallback = _warning_status(policy)
        return fallback if fallback != CheckStatus.PASS else CheckStatus.WARNING
    return CheckStatus.PASS


def site_summaries(
    results: list[CheckResult],
    config: dict[str, Any] | None = None,
) -> list[SiteSummary]:
    grouped: dict[str, list[CheckResult]] = defaultdict(list)
    for result in results:
        if result.site:
            grouped[result.site].append(result)
    return [
        SiteSummary(site=site, status=aggregate_status(items, config), result_count=len(items))
        for site, items in sorted(grouped.items())
    ]


def module_summaries(
    results: list[CheckResult],
    config: dict[str, Any] | None = None,
) -> list[ModuleSummary]:
    grouped: dict[str, list[CheckResult]] = defaultdict(list)
    for result in results:
        grouped[result.module].append(result)
    return [
        ModuleSummary(
            module=module,
            status=aggregate_status(items, config),
            result_count=len(items),
        )
        for module, items in sorted(grouped.items())
    ]


def _aggregate_by_strength(results: list[CheckResult]) -> CheckStatus:
    if not results:
        return CheckStatus.SKIPPED
    return max((result.status for result in results), key=lambda status: STATUS_STRENGTH[status])


def _aggregation_policy(config: dict[str, Any]) -> dict[str, Any]:
    configured = config.get("rules", {}).get("aggregation", {})
    if not isinstance(configured, dict):
        configured = {}
    policy = DEFAULT_POLICY.copy()
    for key in ["fail_blocks", "error_blocks", "manual_review_blocks", "skipped_blocks"]:
        if isinstance(configured.get(key), bool):
            policy[key] = configured[key]
    warning_status = configured.get("warning_overall_status")
    if isinstance(warning_status, str):
        try:
            policy["warning_overall_status"] = CheckStatus(warning_status).value
        except ValueError:
            pass
    return policy


def _warning_status(policy: dict[str, Any]) -> CheckStatus:
    configured = policy.get("warning_overall_status", CheckStatus.WARNING.value)
    try:
        return CheckStatus(configured)
    except ValueError:
        return CheckStatus.WARNING
