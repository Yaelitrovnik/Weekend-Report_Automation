from __future__ import annotations

import socket
import ssl
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.collectors.base import Collector
from app.config.schema import is_unresolved_placeholder
from app.evidence.redaction import configured_secrets, sanitize, sanitize_endpoint
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
        endpoint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.site = site
        self.status_code = status_code
        self.retryable = retryable
        self.endpoint = endpoint

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "endpoint": sanitize_endpoint(self.endpoint or ""),
            "collection_timestamp": iso_now(),
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

        if not site_ids:
            return _blocked_payload(
                "DOCTOR_CONFIGURATION_ERROR",
                "DOCTOR live collection requires at least one valid runtime site",
            )

        site_urls = api.get("sites") or {}

        sites: dict[str, Any] = {}
        errors: list[dict[str, Any]] = []

        for site_id in site_ids:
            url = str(site_urls.get(site_id) or "")
            connection = (api.get("connections") or {}).get(site_id, {})
            try:
                url = _required_url(site_urls.get(site_id), site_id)
                payload = sanitize(
                    self._get_json(site_id, url, connection), configured_secrets(connection)
                )

                sites[site_id] = _normalize_site(
                    site_id,
                    payload,
                )

            except DoctorError as exc:
                exc.endpoint = url
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
        connection: dict[str, Any] | None = None,
    ) -> Any:
        connection = connection or {}
        headers, auth, verify = doctor_connection_options(connection, site_id)
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
                verify=verify,
                auth=auth,
            ) as client:
                response = client.get(
                    url,
                    headers=headers,
                )

        except httpx.TimeoutException as exc:
            raise DoctorError(
                "DOCTOR_TIMEOUT",
                "DOCTOR API request timed out",
                site=site_id,
                retryable=True,
            ) from exc

        except httpx.HTTPError as exc:
            code, description, retryable = classify_transport_error(exc)
            raise DoctorError(code, description, site=site_id, retryable=retryable) from exc

        if response.status_code in {401, 403}:
            raise DoctorError(
                "DOCTOR_AUTH_ERROR",
                f"DOCTOR API returned HTTP {response.status_code}",
                site=site_id,
                status_code=response.status_code,
            )

        if response.status_code >= 300:
            raise DoctorError(
                "DOCTOR_HTTP_ERROR",
                f"DOCTOR API returned HTTP {response.status_code}",
                site=site_id,
                status_code=response.status_code,
                retryable=response.status_code >= 500 or response.status_code in {408, 429},
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

            name = str(entry.get("name") or "health check").strip()

            description = str(entry.get("description") or "").strip()

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

    try:
        parsed = urlsplit(value.strip())
        if (
            parsed.scheme not in {"https", "http"}
            or not parsed.hostname
            or parsed.fragment
            or parsed.username
            or parsed.password
            or is_unresolved_placeholder(value)
        ):
            raise ValueError
        _ = parsed.port
    except ValueError as exc:
        raise DoctorError(
            "DOCTOR_CONFIGURATION_ERROR",
            "Use a backend HTTP(S) API URL without a fragment or embedded credentials",
            site=site_id,
        ) from exc
    return value.strip()


def doctor_connection_options(connection: dict[str, Any], site: str):
    tls = connection.get("tls_verify", True)
    verify: bool | ssl.SSLContext = True
    if tls == "custom_ca":
        ca = connection.get("ca_file")
        if not isinstance(ca, str) or not ca.strip() or is_unresolved_placeholder(ca):
            raise DoctorError(
                "DOCTOR_CONFIGURATION_ERROR", "DOCTOR CA bundle is required", site=site
            )
        try:
            verify = ssl.create_default_context(cafile=ca)
        except (OSError, ssl.SSLError) as exc:
            raise DoctorError(
                "DOCTOR_TLS_ERROR", "DOCTOR CA bundle could not be loaded", site=site
            ) from exc
    elif tls is not True:
        raise DoctorError(
            "DOCTOR_CONFIGURATION_ERROR", "DOCTOR TLS verification must remain enabled", site=site
        )
    kind = connection.get("auth_type", "none")
    headers = {"Accept": "application/json"}
    auth = None
    if kind in {"bearer_token", "x_api_key"}:
        token = connection.get("token")
        if not isinstance(token, str) or not token or is_unresolved_placeholder(token):
            raise DoctorError(
                "DOCTOR_CONFIGURATION_ERROR", "DOCTOR authentication token is required", site=site
            )
        headers["Authorization" if kind == "bearer_token" else "X-API-Key"] = (
            f"Bearer {token}" if kind == "bearer_token" else token
        )
    elif kind == "basic":
        username, password = connection.get("username"), connection.get("password")
        if not username or not password or is_unresolved_placeholder(password):
            raise DoctorError(
                "DOCTOR_CONFIGURATION_ERROR", "DOCTOR Basic credentials are required", site=site
            )
        auth = httpx.BasicAuth(username, password)
    elif kind != "none":
        raise DoctorError(
            "DOCTOR_CONFIGURATION_ERROR", "Unsupported DOCTOR authentication method", site=site
        )
    return headers, auth, verify


def classify_transport_error(exc: Exception) -> tuple[str, str, bool]:
    chain = []
    current: BaseException | None = exc
    while current is not None and current not in chain:
        chain.append(current)
        current = current.__cause__ or current.__context__
    text = " ".join(str(item).lower() for item in chain)
    if any(isinstance(item, ssl.SSLError) for item in chain) or any(
        marker in text for marker in ("certificate", "ssl", "tls")
    ):
        return "DOCTOR_TLS_ERROR", "DOCTOR TLS/certificate verification failed", False
    if any(isinstance(item, socket.gaierror) for item in chain) or any(
        marker in text for marker in ("getaddrinfo", "name or service not known", "name resolution")
    ):
        return "DOCTOR_DNS_ERROR", "DOCTOR hostname resolution failed", True
    if any(isinstance(item, ConnectionRefusedError) for item in chain) or "refused" in text:
        return "DOCTOR_CONNECTION_REFUSED", "DOCTOR connection was refused", True
    return "DOCTOR_CONNECTION_ERROR", "DOCTOR transport connection failed", True


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
