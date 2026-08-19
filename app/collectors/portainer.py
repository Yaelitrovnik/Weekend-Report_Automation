from __future__ import annotations

import os
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import httpx

from app.collectors.base import Collector
from app.config.effective import resolve_portainer_expected
from app.config.schema import UNRESOLVED_PLACEHOLDERS
from app.orchestrator.run_context import RunContext
from app.time_utils import iso_now

SUPPORTED_API_CONTRACTS = {"docker_proxy_v1"}
TOKEN_AUTH_TYPES = {"bearer_token", "jwt", "x_api_key"}
TRANSIENT_STATUS_CODES = {408, 429, 500, 502, 503, 504}
SENSITIVE_KEY_PARTS = {
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
    "x-api-key",
    "apikey",
}


class PortainerError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        site: str | None = None,
        status_code: int | None = None,
        retryable: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.site = site
        self.status_code = status_code
        self.retryable = retryable
        self.metadata = metadata or {}

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "metadata": sanitize_for_evidence(self.metadata),
        }
        if self.site:
            payload["site"] = self.site
        if self.status_code is not None:
            payload["status_code"] = self.status_code
        return payload


@dataclass(slots=True)
class PortainerClientSettings:
    site: str
    base_url: str
    endpoint_id: str
    auth_type: str
    token: str | None
    tls_verify: bool | str
    connect_timeout: float
    read_timeout: float
    retries: int
    retry_backoff_seconds: float
    api_contract: str


class PortainerClient:
    """Read-only Portainer HTTP client. Validation decisions live outside this class."""

    def __init__(
        self,
        settings: PortainerClientSettings,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport

    def get_json(self, path: str) -> Any:
        headers = self._auth_headers()
        timeout = httpx.Timeout(
            connect=self.settings.connect_timeout,
            read=self.settings.read_timeout,
            write=self.settings.read_timeout,
            pool=self.settings.connect_timeout,
        )
        with httpx.Client(
            timeout=timeout,
            verify=self.settings.tls_verify,
            transport=self.transport,
        ) as client:
            return self._get_with_retries(client, path, headers)

    def probe_status(self) -> dict[str, Any]:
        try:
            status = self.get_json("/api/status")
        except PortainerError as exc:
            if exc.code in {
                "PORTAINER_AUTHENTICATION_ERROR",
                "PORTAINER_TLS_ERROR",
                "PORTAINER_TIMEOUT",
            }:
                raise
            return {
                "version_probe": "unavailable",
                "error": exc.to_payload(),
                "api_contract": self.settings.api_contract,
            }
        return {
            "version_probe": "available",
            "status": sanitize_for_evidence(status, [self.settings.token]),
            "api_contract": self.settings.api_contract,
        }

    def _auth_headers(self) -> dict[str, str]:
        auth_type = self.settings.auth_type
        if auth_type in UNRESOLVED_PLACEHOLDERS:
            raise PortainerError(
                "PORTAINER_CONFIGURATION_ERROR",
                "Portainer authentication method is unresolved",
                site=self.settings.site,
            )
        if auth_type == "none":
            return {}
        if auth_type == "bearer_token" or auth_type == "jwt":
            if not self.settings.token:
                raise PortainerError(
                    "PORTAINER_CONFIGURATION_ERROR",
                    "Portainer token environment value is missing",
                    site=self.settings.site,
                )
            return {"Authorization": f"Bearer {self.settings.token}"}
        if auth_type == "x_api_key":
            if not self.settings.token:
                raise PortainerError(
                    "PORTAINER_CONFIGURATION_ERROR",
                    "Portainer token environment value is missing",
                    site=self.settings.site,
                )
            return {"X-API-Key": self.settings.token}
        raise PortainerError(
            "PORTAINER_UNSUPPORTED_API",
            f"Portainer authentication type is not implemented: {auth_type}",
            site=self.settings.site,
        )

    def _get_with_retries(
        self, client: httpx.Client, path: str, headers: dict[str, str]
    ) -> Any:
        attempts = self.settings.retries + 1
        last_error: PortainerError | None = None
        for attempt in range(attempts):
            try:
                response = client.get(self._url(path), headers=headers)
                if response.status_code in {401, 403}:
                    raise PortainerError(
                        "PORTAINER_AUTHENTICATION_ERROR",
                        f"Portainer authentication failed with HTTP {response.status_code}",
                        site=self.settings.site,
                        status_code=response.status_code,
                        metadata={"path": path, "attempt": attempt + 1},
                    )
                if response.status_code in TRANSIENT_STATUS_CODES and attempt + 1 < attempts:
                    last_error = PortainerError(
                        "PORTAINER_COLLECTION_ERROR",
                        f"Portainer transient HTTP {response.status_code}",
                        site=self.settings.site,
                        status_code=response.status_code,
                        retryable=True,
                        metadata={"path": path, "attempt": attempt + 1},
                    )
                    self._sleep_before_retry()
                    continue
                if response.status_code >= 400:
                    raise PortainerError(
                        "PORTAINER_COLLECTION_ERROR",
                        f"Portainer HTTP {response.status_code}",
                        site=self.settings.site,
                        status_code=response.status_code,
                        metadata={"path": path, "attempt": attempt + 1},
                    )
                try:
                    return response.json()
                except ValueError as exc:
                    raise PortainerError(
                        "PORTAINER_INVALID_RESPONSE",
                        "Portainer response was not valid JSON",
                        site=self.settings.site,
                        metadata={"path": path, "attempt": attempt + 1},
                    ) from exc
            except httpx.TimeoutException as exc:
                last_error = PortainerError(
                    "PORTAINER_TIMEOUT",
                    "Portainer request timed out after configured retry policy",
                    site=self.settings.site,
                    retryable=True,
                    metadata={"path": path, "attempt": attempt + 1},
                )
                if attempt + 1 >= attempts:
                    raise last_error from exc
                self._sleep_before_retry()
            except httpx.HTTPError as exc:
                code = (
                    "PORTAINER_TLS_ERROR"
                    if _looks_like_tls_error(exc)
                    else "PORTAINER_COLLECTION_ERROR"
                )
                retryable = code != "PORTAINER_TLS_ERROR"
                last_error = PortainerError(
                    code,
                    "Portainer TLS/certificate error"
                    if code == "PORTAINER_TLS_ERROR"
                    else "Portainer connection failed",
                    site=self.settings.site,
                    retryable=retryable,
                    metadata={"path": path, "attempt": attempt + 1},
                )
                if code == "PORTAINER_TLS_ERROR" or attempt + 1 >= attempts:
                    raise last_error from exc
                self._sleep_before_retry()
        if last_error is not None:
            raise last_error
        raise PortainerError(
            "PORTAINER_COLLECTION_ERROR",
            "Portainer request failed without a response",
            site=self.settings.site,
            metadata={"path": path},
        )

    def _url(self, path: str) -> str:
        normalized = path if path.startswith("/") else f"/{path}"
        return f"{self.settings.base_url.rstrip('/')}{normalized}"

    def _sleep_before_retry(self) -> None:
        if self.settings.retry_backoff_seconds > 0:
            time.sleep(self.settings.retry_backoff_seconds)


class DockerProxyV1Operations:
    def __init__(self, endpoint_id: str) -> None:
        self.endpoint_id = endpoint_id

    def list_services(self, client: PortainerClient) -> Any:
        return client.get_json(f"/api/endpoints/{self.endpoint_id}/docker/services")

    def list_tasks(self, client: PortainerClient) -> Any:
        return client.get_json(f"/api/endpoints/{self.endpoint_id}/docker/tasks")


class PortainerCollector(Collector):
    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self.transport = transport

    def collect(self, context: RunContext) -> dict[str, Any]:
        config = context.config.get("portainer_expected", {})
        mode = config.get("collection_mode") or (
            "fixture" if config.get("fixture_actual") is not None else "live"
        )
        if mode == "fixture":
            return _collect_fixture(config, context)
        if mode != "live":
            return _collection_with_error(
                "PORTAINER_CONFIGURATION_ERROR",
                f"Unsupported Portainer collection_mode: {mode}",
                context,
            )
        return self._collect_live(config, context)

    def _collect_live(self, config: dict[str, Any], context: RunContext) -> dict[str, Any]:
        config = resolve_portainer_expected(config)
        sites: dict[str, Any] = {}
        errors: list[dict[str, Any]] = []
        for site_id, site_config in (config.get("sites") or {}).items():
            try:
                settings = _client_settings(site_id, site_config)
                operations = _operations(settings)
                client = PortainerClient(settings, transport=self.transport)
                api_metadata = client.probe_status()
                raw_services = operations.list_services(client)
                raw_tasks = operations.list_tasks(client)
                sites[site_id] = normalize_swarm_site(
                    site_id,
                    site_config,
                    raw_services,
                    raw_tasks,
                    api_metadata=api_metadata,
                )
            except PortainerError as exc:
                errors.append(exc.to_payload())
        return {
            "mode": "live",
            "collection_timestamp": iso_now(),
            "metadata": {
                "source": "portainer_api",
                "configuration_hash": context.config.get("_config_hash"),
                "read_only": True,
            },
            "sites": sites,
            "errors": errors,
        }


def _collect_fixture(config: dict[str, Any], context: RunContext) -> dict[str, Any]:
    fixture = sanitize_for_evidence(config.get("fixture_actual") or {})
    fixture_sites = fixture.get("sites", fixture) if isinstance(fixture, dict) else {}
    sites = {
        site_id: _normalize_fixture_site(site_id, site_config)
        for site_id, site_config in fixture_sites.items()
        if isinstance(site_config, dict)
    }
    return {
        "mode": "fixture",
        "collection_timestamp": iso_now(),
        "metadata": {
            "source": "fixture",
            "configuration_hash": context.config.get("_config_hash"),
            "read_only": True,
        },
        "sites": sites,
        "errors": [],
    }


def _collection_with_error(code: str, message: str, context: RunContext) -> dict[str, Any]:
    return {
        "mode": "configuration_error",
        "collection_timestamp": iso_now(),
        "metadata": {
            "source": "configuration",
            "configuration_hash": context.config.get("_config_hash"),
            "read_only": True,
        },
        "sites": {},
        "errors": [{"code": code, "message": message, "retryable": False}],
    }


def _client_settings(site_id: str, site_config: dict[str, Any]) -> PortainerClientSettings:
    connection = site_config.get("connection") or {}
    tls = connection.get("tls") or {}
    auth = connection.get("auth") or {}
    timeout_cfg = connection.get("timeouts") or {}
    retry_cfg = connection.get("retries") or {}
    url_env = connection.get("url_env")
    token_env = auth.get("token_env") or connection.get("token_env")
    auth_type = auth.get("type") or connection.get("auth_type")
    endpoint_id = connection.get("endpoint_id")
    api_contract = connection.get("api_contract") or site_config.get("api_contract")
    base_url = _env_required(url_env, site_id, "Portainer URL")
    token = (
        _env_required(token_env, site_id, "Portainer token")
        if auth_type in TOKEN_AUTH_TYPES
        else None
    )
    return PortainerClientSettings(
        site=site_id,
        base_url=base_url,
        endpoint_id=_required_string(endpoint_id, site_id, "endpoint_id"),
        auth_type=_required_string(auth_type, site_id, "auth.type"),
        token=token,
        tls_verify=_tls_verify(tls, site_id),
        connect_timeout=_number(
            timeout_cfg.get("connect_seconds"),
            site_id,
            "timeouts.connect_seconds",
        ),
        read_timeout=_number(timeout_cfg.get("read_seconds"), site_id, "timeouts.read_seconds"),
        retries=_integer(retry_cfg.get("attempts"), site_id, "retries.attempts"),
        retry_backoff_seconds=_number(
            retry_cfg.get("backoff_seconds"), site_id, "retries.backoff_seconds"
        ),
        api_contract=_required_string(api_contract, site_id, "api_contract"),
    )


def _operations(settings: PortainerClientSettings) -> DockerProxyV1Operations:
    if settings.api_contract not in SUPPORTED_API_CONTRACTS:
        raise PortainerError(
            "PORTAINER_UNSUPPORTED_API",
            f"Unsupported Portainer API contract: {settings.api_contract}",
            site=settings.site,
            metadata={"supported_contracts": sorted(SUPPORTED_API_CONTRACTS)},
        )
    return DockerProxyV1Operations(settings.endpoint_id)


def normalize_swarm_site(
    site_id: str,
    site_config: dict[str, Any],
    raw_services: Any,
    raw_tasks: Any,
    *,
    api_metadata: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(raw_services, list) or not isinstance(raw_tasks, list):
        raise PortainerError(
            "PORTAINER_INVALID_RESPONSE",
            "Portainer Docker services/tasks response was not a list",
            site=site_id,
        )
    tasks_by_service: dict[str, list[dict[str, Any]]] = {}
    for task in raw_tasks:
        if not isinstance(task, dict):
            continue
        service_id = str(task.get("ServiceID") or task.get("service_id") or "")
        if service_id:
            tasks_by_service.setdefault(service_id, []).append(task)
    services = []
    for service in raw_services:
        if not isinstance(service, dict):
            raise PortainerError(
                "PORTAINER_INVALID_RESPONSE",
                "Portainer service entry was not an object",
                site=site_id,
            )
        normalized = _normalize_service(site_id, site_config, service, tasks_by_service)
        services.append(normalized)
    return {
        "site": site_id,
        "environment_type": site_config.get("environment_type", "docker_swarm"),
        "collection_timestamp": iso_now(),
        "api": sanitize_for_evidence(api_metadata),
        "raw_api": {
            "services": sanitize_for_evidence(raw_services),
            "tasks": sanitize_for_evidence(raw_tasks),
        },
        "services": services,
    }


def _normalize_service(
    site_id: str,
    site_config: dict[str, Any],
    service: dict[str, Any],
    tasks_by_service: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    spec = service.get("Spec") or {}
    task_template = spec.get("TaskTemplate") or {}
    container_spec = task_template.get("ContainerSpec") or {}
    mode_obj = spec.get("Mode") or {}
    replicated = mode_obj.get("Replicated") or {}
    service_id = str(service.get("ID") or service.get("Id") or service.get("id") or "")
    name = str(spec.get("Name") or service.get("Name") or service.get("name") or service_id)
    labels = spec.get("Labels") or service.get("Labels") or {}
    stack = labels.get("com.docker.stack.namespace")
    desired_replicas = replicated.get("Replicas")
    service_tasks = tasks_by_service.get(service_id, [])
    task_states = [_normalize_task(task) for task in service_tasks]
    running_replicas = sum(1 for task in task_states if task.get("current_state") == "running")
    health_values = [
        task.get("health")
        for task in task_states
        if task.get("health") not in (None, "", "unknown")
    ]
    health_available = bool(health_values)
    healthy_replicas = (
        sum(
            1
            for task in task_states
            if task.get("current_state") == "running" and task.get("health") == "healthy"
        )
        if health_available
        else None
    )
    failed_tasks = sum(1 for task in task_states if task.get("current_state") == "failed")
    rejected_tasks = sum(1 for task in task_states if task.get("current_state") == "rejected")
    restarting_tasks = sum(1 for task in task_states if task.get("current_state") == "restarting")
    starting_tasks = sum(1 for task in task_states if task.get("current_state") == "starting")
    update_status = service.get("UpdateStatus") or {}
    service_state = update_status.get("State") or service.get("service_state") or "active"
    return {
        "site": site_id,
        "id": service_id,
        "name": name,
        "stack": stack,
        "desired_replicas": desired_replicas,
        "running_replicas": running_replicas,
        "healthy_replicas": healthy_replicas,
        "health": {
            "available": health_available,
            "source": "docker_task_container_health"
            if health_available
            else "not_available_from_response",
            "definition": (
                "healthy replicas are running tasks with Docker container health status healthy"
                if health_available
                else "not derived; Docker/Portainer response did not expose task health status"
            ),
        },
        "image": container_spec.get("Image") or service.get("Image") or service.get("image"),
        "service_mode": "replicated" if "Replicated" in mode_obj else "global",
        "service_state": service_state,
        "task_states": task_states,
        "failed_tasks": failed_tasks,
        "rejected_tasks": rejected_tasks,
        "restarting_tasks": restarting_tasks,
        "starting_tasks": starting_tasks,
        "collection_timestamp": iso_now(),
        "metadata": {
            "portainer_target": "swarm_service",
            "environment_type": site_config.get("environment_type", "docker_swarm"),
        },
    }


def _normalize_task(task: dict[str, Any]) -> dict[str, Any]:
    status = task.get("Status") or {}
    container_status = status.get("ContainerStatus") or {}
    health = container_status.get("Health") or {}
    return {
        "id": task.get("ID") or task.get("Id") or task.get("id"),
        "desired_state": task.get("DesiredState") or task.get("desired_state"),
        "current_state": status.get("State") or task.get("state"),
        "health": health.get("Status") or task.get("health"),
        "error": status.get("Err") or task.get("error"),
        "message": status.get("Message") or task.get("message"),
        "node_id": task.get("NodeID") or task.get("node_id"),
        "slot": task.get("Slot") or task.get("slot"),
    }


def _normalize_fixture_site(site_id: str, site_config: dict[str, Any]) -> dict[str, Any]:
    services = []
    for service in site_config.get("services", []):
        service = dict(service)
        service.setdefault("site", site_id)
        service.setdefault("collection_timestamp", iso_now())
        service.setdefault("task_states", [])
        service.setdefault("failed_tasks", 0)
        service.setdefault("rejected_tasks", 0)
        service.setdefault("restarting_tasks", 0)
        service.setdefault("starting_tasks", 0)
        service.setdefault("service_state", "active")
        service.setdefault("service_mode", "replicated")
        if "health" not in service:
            health_available = service.get("healthy_replicas") is not None
            service["health"] = {
                "available": health_available,
                "source": "fixture" if health_available else "not_available_from_fixture",
                "definition": "fixture-provided healthy replica count"
                if health_available
                else "not derived in fixture",
            }
        services.append(service)
    return {
        "site": site_id,
        "environment_type": site_config.get("environment_type", "docker_swarm"),
        "collection_timestamp": site_config.get("collection_timestamp") or iso_now(),
        "api": sanitize_for_evidence(site_config.get("api", {"source": "fixture"})),
        "raw_api": sanitize_for_evidence(site_config.get("raw_api", {})),
        "services": services,
    }


def sanitize_for_evidence(value: Any, secrets: Iterable[str | None] = ()) -> Any:
    secret_values = [secret for secret in secrets if secret]
    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            key_text = str(key)
            if _sensitive_key(key_text):
                sanitized[key_text] = "<REDACTED>"
            else:
                sanitized[key_text] = sanitize_for_evidence(item, secret_values)
        return sanitized
    if isinstance(value, list):
        return [sanitize_for_evidence(item, secret_values) for item in value]
    if isinstance(value, str):
        redacted = value
        for secret in secret_values:
            redacted = redacted.replace(secret, "<REDACTED>")
        return redacted
    return value


def _sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("_", "-")
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def _required_string(value: Any, site: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or value in UNRESOLVED_PLACEHOLDERS:
        raise PortainerError(
            "PORTAINER_CONFIGURATION_ERROR",
            f"Portainer {field} is required for live collection",
            site=site,
        )
    return value.strip()


def _env_required(env_name: Any, site: str, label: str) -> str:
    name = _required_string(env_name, site, f"{label} env reference")
    value = os.getenv(name, "").strip()
    if not value or value in UNRESOLVED_PLACEHOLDERS:
        raise PortainerError(
            "PORTAINER_CONFIGURATION_ERROR",
            f"{label} runtime environment variable is missing or unresolved: {name}",
            site=site,
            metadata={"env": name},
        )
    return value


def _tls_verify(tls: dict[str, Any], site: str) -> bool | str:
    value = tls.get("verify")
    if value in UNRESOLVED_PLACEHOLDERS or value is None:
        raise PortainerError(
            "PORTAINER_CONFIGURATION_ERROR",
            "Portainer TLS verification policy is unresolved",
            site=site,
        )
    if value is True:
        return True
    if value == "custom_ca":
        ca_env = _required_string(tls.get("ca_file_env"), site, "tls.ca_file_env")
        ca_file = os.getenv(ca_env, "").strip()
        if not ca_file or ca_file in UNRESOLVED_PLACEHOLDERS:
            raise PortainerError(
                "PORTAINER_CONFIGURATION_ERROR",
                f"Portainer CA certificate path is missing or unresolved: {ca_env}",
                site=site,
                metadata={"env": ca_env},
            )
        return ca_file
    if value is False:
        raise PortainerError(
            "PORTAINER_CONFIGURATION_ERROR",
            "Portainer TLS verify=false is not allowed for production live collection",
            site=site,
        )
    raise PortainerError(
        "PORTAINER_CONFIGURATION_ERROR",
        f"Unsupported Portainer TLS verification policy: {value}",
        site=site,
    )


def _number(value: Any, site: str, field: str) -> float:
    if isinstance(value, int | float) and value >= 0:
        return float(value)
    raise PortainerError(
        "PORTAINER_CONFIGURATION_ERROR",
        f"Portainer {field} must be a non-negative number",
        site=site,
    )


def _integer(value: Any, site: str, field: str) -> int:
    if isinstance(value, int) and value >= 0:
        return value
    raise PortainerError(
        "PORTAINER_CONFIGURATION_ERROR",
        f"Portainer {field} must be a non-negative integer",
        site=site,
    )


def _looks_like_tls_error(exc: httpx.HTTPError) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in ["ssl", "tls", "certificate", "cert_verify"])
