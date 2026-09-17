from __future__ import annotations

from typing import Any

from app.domain import CheckResult, CheckStatus
from app.orchestrator.run_context import RunContext
from app.time_utils import iso_now
from app.validators.base import Validator


class DoctorValidator(Validator):
    def validate(
        self, actual: dict[str, Any], config: dict[str, Any], context: RunContext
    ) -> list[CheckResult]:
        started = iso_now()
        doctor_config = config.get("doctor", {}).get("doctor", {})
        if actual.get("mode") == "manual":
            return [
                _result(
                    context.run_id,
                    "manual_review",
                    None,
                    "doctor",
                    doctor_config.get("manual_review", {}),
                    actual.get("manual_review", {}),
                    CheckStatus.MANUAL_REVIEW,
                    (
                        "DOCTOR is configured for manual review; reviewer notes are "
                        "required by policy if configured."
                    ),
                    started,
                )
            ]

        results: list[CheckResult] = []
        for error in actual.get("errors") or []:
            results.append(
                _result(
                    context.run_id,
                    "collection",
                    error.get("site"),
                    error.get("site") or "doctor",
                    {"mode": "api"},
                    error,
                    CheckStatus.ERROR,
                    f"{error.get('code', 'DOCTOR_COLLECTION_ERROR')}: {error.get('message')}",
                    started,
                    metadata={
                        "error_code": error.get("code"),
                        "doctor_error_type": "technical",
                    },
                )
            )

        if results:
            results.append(
                _module_result(context.run_id, doctor_config, actual, CheckStatus.ERROR, started)
            )
            return results

        validation = doctor_config.get("validation", {})
        sites = actual.get("sites", {})
        runtime_sites = _runtime_site_ids(config)
        if not isinstance(sites, dict) or not sites or len(runtime_sites) != 2:
            results.append(
                _result(
                    context.run_id,
                    "collection",
                    None,
                    "doctor",
                    {"runtime_sites": runtime_sites, "site_count": 2},
                    actual,
                    CheckStatus.ERROR,
                    "DOCTOR API collection did not return reliable site data",
                    started,
                    metadata={"doctor_error_type": "technical"},
                )
            )
            results.append(
                _module_result(context.run_id, doctor_config, actual, CheckStatus.ERROR, started)
            )
            return results

        for site in runtime_sites:
            site_actual = sites.get(site)
            if not isinstance(site_actual, dict):
                results.append(
                    _result(
                        context.run_id,
                        "collection",
                        site,
                        site,
                        {"site": site},
                        site_actual,
                        CheckStatus.ERROR,
                        "DOCTOR site response was missing or malformed",
                        started,
                        metadata={"doctor_error_type": "technical"},
                    )
                )
                continue

            services = _services_by_name(
                site_actual.get("services", site_actual.get("service_health"))
            )
            if not services:
                results.append(
                    _result(
                        context.run_id,
                        "collection",
                        site,
                        site,
                        {"discovered_services": ">=1"},
                        {"discovered_services": []},
                        CheckStatus.ERROR,
                        "DOCTOR site response contained no discoverable services",
                        started,
                        metadata={
                            "doctor_error_type": "technical",
                            "error_code": "DOCTOR_NO_SERVICES_DISCOVERED",
                        },
                    )
                )
                continue

            for service in sorted(services):
                results.append(_presence_result(context.run_id, site, service, started))
                results.append(
                    _service_result(
                        context.run_id,
                        site,
                        service,
                        services[service],
                        validation,
                        started,
                    )
                )

        module_status = _module_status(results, validation)
        results.append(
            _module_result(context.run_id, doctor_config, actual, module_status, started)
        )
        return results


def _presence_result(run_id: str, site: str, service: str, started: str) -> CheckResult:
    return _result(
        run_id,
        "service.exists",
        site,
        service,
        {"exists": True},
        {"exists": True, "service": service},
        CheckStatus.PASS,
        "DOCTOR service discovered",
        started,
    )


def _service_result(
    run_id: str,
    site: str,
    service: str,
    actual: dict[str, Any],
    validation: dict[str, Any],
    started: str,
) -> CheckResult:
    healthy = actual.get("healthy")
    if healthy is True:
        return _result(
            run_id,
            "service.health",
            site,
            service,
            {"service": service, "healthy": True},
            actual,
            _status(validation.get("healthy_status"), CheckStatus.PASS),
            "DOCTOR service is healthy",
            started,
        )
    if healthy is False:
        reason = actual.get("reason") or actual.get("message") or actual.get("error")

        if validation.get("unhealthy_reason_required", False) and not str(reason or "").strip():
            return _result(
                run_id,
                "service.health",
                site,
                service,
                {"service": service, "healthy": True, "reason_required": True},
                actual,
                CheckStatus.ERROR,
                "DOCTOR service is unhealthy but no reason was supplied",
                started,
                metadata={
                    "doctor_error_type": "technical",
                    "error_code": "DOCTOR_UNHEALTHY_REASON_MISSING",
                },
            )

        return _result(
            run_id,
            "service.health",
            site,
            service,
            {"service": service, "healthy": True, "reason_required": True},
            actual,
            _status(validation.get("unhealthy_status"), CheckStatus.ERROR),
            f"DOCTOR service is unhealthy: {reason}",
            started,
            metadata={
                "doctor_error_type": "health_issue",
                "reviewable_health_issue": True,
                "issue": "unhealthy_service",
                "reason": reason,
            },
        )

    return _result(
        run_id,
        "service.health",
        site,
        service,
        {"service": service, "healthy": True},
        actual,
        CheckStatus.ERROR,
        "DOCTOR service health state is unknown or unparseable",
        started,
        metadata={
            "doctor_error_type": "technical",
            "error_code": "DOCTOR_UNKNOWN_HEALTH_STATE",
        },
    )


def _module_status(results: list[CheckResult], validation: dict[str, Any]) -> CheckStatus:
    if any(result.metadata.get("doctor_error_type") == "technical" for result in results):
        return CheckStatus.ERROR
    if any(result.metadata.get("reviewable_health_issue") for result in results):
        return _status(validation.get("any_unhealthy_status"), CheckStatus.MANUAL_REVIEW)
    return _status(validation.get("all_healthy_status"), CheckStatus.PASS)


def _module_result(
    run_id: str,
    doctor_config: dict[str, Any],
    actual: dict[str, Any],
    status: CheckStatus,
    started: str,
) -> CheckResult:
    return _result(
        run_id,
        "module_status",
        None,
        "doctor",
        {
            "mode": doctor_config.get("mode"),
            "service_inventory": "dynamic_discovery",
        },
        {"mode": actual.get("mode"), "site_count": len(actual.get("sites", {}) or {})},
        status,
        "DOCTOR automated services require human review"
        if status == CheckStatus.MANUAL_REVIEW
        else f"DOCTOR overall {status.value}",
        started,
        metadata={
            "doctor_manual_review_path": status == CheckStatus.MANUAL_REVIEW,
            "technical_error": status == CheckStatus.ERROR,
        },
    )


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


def _services_by_name(value: Any) -> dict[str, dict[str, Any]]:
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items() if isinstance(item, dict)}
    if isinstance(value, list):
        services: dict[str, dict[str, Any]] = {}
        for item in value:
            if not isinstance(item, dict):
                continue
            name = item.get("name") or item.get("service")
            if isinstance(name, str):
                services[name] = item
        return services
    return {}


def _status(value: Any, default: CheckStatus) -> CheckStatus:
    try:
        return CheckStatus(str(value))
    except ValueError:
        return default


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
    cleaned_metadata = {k: v for k, v in (metadata or {}).items() if v is not None}
    return CheckResult(
        run_id=run_id,
        module="doctor",
        check_id=f"doctor.{check}",
        site=site,
        target=target,
        expected=expected,
        actual=actual,
        status=status,
        message=message,
        started_at=started,
        finished_at=iso_now(),
        metadata=cleaned_metadata,
    )
