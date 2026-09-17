from __future__ import annotations

from typing import Any

import httpx

from app.collectors.base import Collector
from app.orchestrator.run_context import RunContext
from app.time_utils import iso_now

STATUS_BY_NUMBER = {
    0: "Unhealthy",
    1: "Degraded",
    2: "Healthy",
}


class DoctorError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        site: str | None = None,
        status_code: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.site = site
        self.status_code = status_code
        self.retryable = retryable

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }

        if self.site:
            payload["site"] = self.site

        if self.status_code is not None:
            payload["status_code"] = self.status_code

        return payload


class DoctorCollector(Collector):
    def __init__(
        self,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.transport = transport

    def collect(self, context: RunContext) -> dict[str, Any]:
        doctor = context.config.get("doctor", {}).get("doctor", {})

        if doctor.get("mode") == "manual":
            return {
                "mode": "manual",
                "manual_review": doctor.get("manual_review", {}),
            }

        fixture = doctor.get("fixture_actual")

        if isinstance(fixture, dict):
            return {
                "mode": "api",
                "collection_timestamp": iso_now(),
                "sites": fixture.get("sites", fixture),
                "errors": [],
            }

        return self._collect_live(doctor, context)

    def _collect_live(
        self,
        doctor: dict[str, Any],
        context: RunContext,
    ) -> dict[str, Any]:
        api = doctor.get("api") or {}
        site_ids = _runtime_site_ids(context.config)

        if len(site_ids) != 2:
            return _blocked_payload(
                "DOCTOR_CONFIGURATION_ERROR",
                "DOCTOR live collection requires exactly two runtime sites",
            )

        site_urls = [
            api.get("site1_url"),
            api.get("site2_url"),
        ]

        sites: dict[str, Any] = {}
        errors: list[dict[str, Any]] = []

        for site_id, raw_url in zip(site_ids, site_urls, strict=False):
            try:
                url = _required_url(raw_url, site_id)
                payload = self._get_json(site_id, url)

                sites[site_id] = _normalize_site(
                    site_id,
                    payload,
                )

            except DoctorError as exc:
                errors.append(exc.to_payload())

        return {
            "mode": "api",
            "collection_timestamp": iso_now(),
            "metadata": {
                "source": "doctor_api",
                "read_only": True,
                "configuration_hash": context.config.get("_config_hash"),
            },
            "sites": sites,
            "errors": errors,
        }

    def _get_json(
        self,
        site_id: str,
        url: str,
    ) -> Any:
        timeout = httpx.Timeout(
            connect=5.0,
            read=20.0,
            write=20.0,
            pool=5.0,
        )

        try:
            with httpx.Client(
                timeout=timeout,
                transport=self.transport,
            ) as client:
                response = client.get(
                    url,
                    headers={"Accept": "application/json"},
                )

        except httpx.TimeoutException as exc:
            raise DoctorError(
                "DOCTOR_TIMEOUT",
                "DOCTOR API request timed out",
                site=site_id,
                retryable=True,
            ) from exc

        except httpx.HTTPError as exc:
            raise DoctorError(
                "DOCTOR_CONNECTION_ERROR",
                "DOCTOR API connection failed",
                site=site_id,
                retryable=True,
            ) from exc

        if response.status_code in {401, 403}:
            raise DoctorError(
                "DOCTOR_AUTHENTICATION_ERROR",
                f"DOCTOR API returned HTTP {response.status_code}",
                site=site_id,
                status_code=response.status_code,
            )

        if response.status_code >= 400:
            raise DoctorError(
                "DOCTOR_HTTP_ERROR",
                f"DOCTOR API returned HTTP {response.status_code}",
                site=site_id,
                status_code=response.status_code,
                retryable=response.status_code >= 500,
            )

        try:
            return response.json()

        except ValueError as exc:
            raise DoctorError(
                "DOCTOR_INVALID_RESPONSE",
                "DOCTOR API response was not valid JSON",
                site=site_id,
            ) from exc


def _normalize_site(
    site_id: str,
    payload: Any,
) -> dict[str, Any]:
    if not isinstance(payload, list):
        raise DoctorError(
            "DOCTOR_INVALID_RESPONSE",
            "DOCTOR API response must be a JSON list",
            site=site_id,
        )

    services: dict[str, dict[str, Any]] = {}

    for execution in payload:
        if not isinstance(execution, dict):
            raise DoctorError(
                "DOCTOR_INVALID_RESPONSE",
                "DOCTOR API service entry was not an object",
                site=site_id,
            )

        name = str(execution.get("name") or "").strip()

        if not name:
            raise DoctorError(
                "DOCTOR_INVALID_RESPONSE",
                "DOCTOR API service entry has no name",
                site=site_id,
            )

        if name in services:
            raise DoctorError(
                "DOCTOR_INVALID_RESPONSE",
                f"DOCTOR API returned duplicate service name: {name}",
                site=site_id,
            )

        status = _normalize_status(execution.get("status"))

        if status is None:
            raise DoctorError(
                "DOCTOR_INVALID_RESPONSE",
                f"DOCTOR service {name!r} has an unknown status",
                site=site_id,
            )

        healthy = status == "Healthy"

        service: dict[str, Any] = {
            "healthy": healthy,
            "status": status,
            "entries": execution.get("entries") or [],
        }

        if not healthy:
            service["reason"] = _failure_reason(
                execution,
                status,
            )

        services[name] = service

    if not services:
        raise DoctorError(
            "DOCTOR_NO_SERVICES_DISCOVERED",
            "DOCTOR API returned no services",
            site=site_id,
        )

    return {
        "site": site_id,
        "collection_timestamp": iso_now(),
        "services": services,
        "raw_api": payload,
    }


def _normalize_status(value: Any) -> str | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return STATUS_BY_NUMBER.get(value)

    if not isinstance(value, str):
        return None

    normalized = value.strip().lower()

    if normalized == "healthy":
        return "Healthy"

    if normalized == "degraded":
        return "Degraded"

    if normalized == "unhealthy":
        return "Unhealthy"

    return None


def _failure_reason(
    execution: dict[str, Any],
    overall_status: str,
) -> str:
    entries = execution.get("entries") or []

    reasons: list[str] = []

    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict):
                continue

            status = _normalize_status(entry.get("status"))

            if status == "Healthy":
                continue

            name = str(
                entry.get("name") or "health check"
            ).strip()

            description = str(
                entry.get("description") or ""
            ).strip()

            if description:
                reasons.append(f"{name}: {description}")
            elif status:
                reasons.append(f"{name}: {status}")

    if reasons:
        return "; ".join(reasons)

    return f"DOCTOR service status is {overall_status}"


def _runtime_site_ids(
    config: dict[str, Any],
) -> list[str]:
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


def _required_url(
    value: Any,
    site_id: str,
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DoctorError(
            "DOCTOR_CONFIGURATION_ERROR",
            "DOCTOR API URL is missing",
            site=site_id,
        )

    return value.strip()


def _blocked_payload(
    code: str,
    message: str,
) -> dict[str, Any]:
    return {
        "mode": "api",
        "collection_timestamp": iso_now(),
        "sites": {},
        "errors": [
            {
                "code": code,
                "message": message,
                "retryable": False,
            }
        ],
    }