from __future__ import annotations

from typing import Any

from app.domain import CheckResult, CheckStatus
from app.orchestrator.run_context import RunContext
from app.time_utils import iso_now
from app.validators.base import Validator

STEP_FIELDS = {
    "create": "create_success",
    "replication_after_create": "replication_after_create",
    "delete": "delete_success",
    "replication_after_delete": "replication_after_delete",
    "cleanup": "cleanup_complete",
}


class DatabaseValidator(Validator):
    def validate(
        self, actual: dict[str, Any], config: dict[str, Any], context: RunContext
    ) -> list[CheckResult]:
        started = iso_now()
        if actual.get("error"):
            return [
                _r(
                    context.run_id,
                    "sync_execution",
                    None,
                    "database_sync_test",
                    {"adapter": "existing_sync_function"},
                    actual,
                    CheckStatus.ERROR,
                    actual["error"],
                    started,
                    metadata={"errors": actual.get("errors", [])},
                )
            ]
        results: list[CheckResult] = []
        for site, observed in actual.get("sites", {}).items():
            if not isinstance(observed, dict):
                results.append(
                    _r(
                        context.run_id,
                        "sync_execution",
                        site,
                        site,
                        {"structured_result": True},
                        observed,
                        CheckStatus.ERROR,
                        "database sync result is malformed",
                        started,
                    )
                )
                continue
            site_results = _validate_site(context.run_id, site, observed, started)
            results.extend(site_results)
        return results


def _validate_site(
    run_id: str,
    site: str,
    observed: dict[str, Any],
    started: str,
) -> list[CheckResult]:
    target = str(observed.get("target") or "database_sync_test")
    results = []
    errors = observed.get("errors") or []
    for check, field in STEP_FIELDS.items():
        value = observed.get(field)
        if value is True:
            status = CheckStatus.PASS
            message = f"{check} succeeded"
        elif value is False:
            status = CheckStatus.FAIL
            message = f"{check} failed"
        else:
            status = CheckStatus.ERROR
            message = f"{check} could not be determined"
        if errors and status == CheckStatus.PASS:
            message = f"{check} succeeded; non-blocking errors recorded"
        results.append(
            _r(
                run_id,
                check,
                site,
                target,
                {"expected": True, "field": field},
                {"value": value, "errors": errors},
                status,
                message,
                started,
                metadata={"error_count": len(errors)},
            )
        )
    module_status = _aggregate(results, errors)
    results.append(
        _r(
            run_id,
            "module_status",
            site,
            target,
            {"all_steps": STEP_FIELDS},
            observed,
            module_status,
            "database sync structured module status",
            started,
            metadata={"error_count": len(errors)},
        )
    )
    return results


def _aggregate(results: list[CheckResult], errors: list[Any]) -> CheckStatus:
    statuses = [result.status for result in results]
    if CheckStatus.ERROR in statuses:
        return CheckStatus.ERROR
    if CheckStatus.FAIL in statuses:
        return CheckStatus.FAIL
    if errors:
        return CheckStatus.WARNING
    return CheckStatus.PASS


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
        "database",
        f"database.{check}",
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
