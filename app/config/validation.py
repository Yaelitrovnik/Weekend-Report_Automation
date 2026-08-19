from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from app.config.effective import resolve_portainer_expected, resolve_rabbitmq_expected
from app.config.schema import (
    ALL_PLACEHOLDERS,
    NOT_APPLICABLE_ALLOWED_PATH_PARTS,
    PLACEHOLDER_NOT_APPLICABLE,
    UNRESOLVED_PLACEHOLDERS,
    VALID_CHECK_STATUSES,
    VALID_MODULES,
)
from app.runtime_identity import runtime_identity_errors

UNSET_RUNTIME_VALUES = {"", "<TBD>", "<TO_VERIFY>", "UNKNOWN"}
PORTAINER_SUPPORTED_COLLECTION_MODES = {"fixture", "live"}
PORTAINER_SUPPORTED_AUTH_TYPES = {"bearer_token", "jwt", "x_api_key", "none"}
PORTAINER_SUPPORTED_API_CONTRACTS = {"docker_proxy_v1"}
PORTAINER_SUPPORTED_IMAGE_COMPARISONS = {"full_reference", "repository_tag", "digest"}
RABBITMQ_SUPPORTED_COLLECTION_MODES = {"fixture", "live"}
RABBITMQ_SUPPORTED_DESTINATION_TYPES = {"queue", "exchange"}
DATABASE_SUPPORTED_COLLECTION_MODES = {"fixture", "live"}
PORTAINER_PARITY_FIELDS = {
    "service_presence",
    "desired_replicas",
    "running_replicas",
    "healthy_replicas",
    "image",
    "service_state",
}


@dataclass(slots=True)
class ValidationIssue:
    path: str
    message: str
    severity: str = "ERROR"

    def render(self) -> str:
        return f"{self.severity}: {self.path}: {self.message}"


@dataclass(slots=True)
class ValidationReport:
    errors: list[ValidationIssue] = field(default_factory=list)
    warnings: list[ValidationIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def add_error(self, path: str, message: str) -> None:
        self.errors.append(ValidationIssue(path, message, "ERROR"))

    def add_warning(self, path: str, message: str) -> None:
        self.warnings.append(ValidationIssue(path, message, "WARNING"))

    def lines(self) -> list[str]:
        return [i.render() for i in self.errors + self.warnings]


def iter_leaves(value: Any, prefix: str = ""):
    if isinstance(value, dict):
        for key, item in value.items():
            new_prefix = f"{prefix}.{key}" if prefix else str(key)
            yield from iter_leaves(item, new_prefix)
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            yield from iter_leaves(item, f"{prefix}[{idx}]")
    else:
        yield prefix, value


def _module_rules(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    modules = config.get("rules", {}).get("modules", {})
    if not isinstance(modules, dict):
        return {}
    return modules


def _site_ids(config: dict[str, Any]) -> set[str]:
    sites = config.get("sites", {}).get("sites", [])
    if not isinstance(sites, list):
        return set()
    site_ids: set[str] = set()
    for site in sites:
        if isinstance(site, dict) and isinstance(site.get("id"), str):
            site_ids.add(site["id"])
    return site_ids


def _has_enabled_required_automation(config: dict[str, Any]) -> bool:
    for name, module in _module_rules(config).items():
        if name == "doctor" and config.get("doctor", {}).get("doctor", {}).get("mode") == "manual":
            continue
        if module.get("enabled") and module.get("required"):
            return True
    return False


def validate_config(
    config: dict[str, Any], *, production_preflight: bool = True
) -> ValidationReport:
    report = ValidationReport()
    sites = config.get("sites", {}).get("sites")
    if not isinstance(sites, list) or not sites:
        report.add_error("sites.sites", "must contain at least one site")
    else:
        seen: set[str] = set()
        for idx, site in enumerate(sites):
            if not isinstance(site, dict):
                report.add_error(f"sites.sites[{idx}]", "site must be an object")
                continue
            site_id = site.get("id")
            if not site_id:
                report.add_error(f"sites.sites[{idx}].id", "site id is required")
            elif site_id in seen:
                report.add_error(f"sites.sites[{idx}].id", f"duplicate site id {site_id!r}")
            else:
                seen.add(site_id)

    modules = _module_rules(config)
    for module_name, module in modules.items():
        if module_name not in VALID_MODULES:
            report.add_error(f"rules.modules.{module_name}", "unknown module")
        if module.get("if_unavailable_status") not in VALID_CHECK_STATUSES:
            report.add_error(
                f"rules.modules.{module_name}.if_unavailable_status",
                "invalid status enum",
            )

    _validate_aggregation(config, report)
    _validate_parity(config, report)
    _validate_review_policy(config, report)
    site_ids = _site_ids(config)
    for area in ["portainer_expected", "rabbitmq_expected", "recording", "servers"]:
        by_site = config.get(area, {}).get("sites")
        if isinstance(by_site, dict):
            for site_id in by_site:
                if site_id not in site_ids:
                    report.add_error(f"{area}.sites.{site_id}", "unknown site reference")

    _validate_splunk(config, report)
    _validate_portainer(config, report, production_preflight=production_preflight)
    _validate_rabbitmq(config, report, production_preflight=production_preflight)
    _validate_database(config, report, production_preflight=production_preflight)
    _validate_thresholds(config, report)
    _validate_runtime_environment(config, report, production_preflight=production_preflight)
    _validate_placeholders(config, report, production_preflight=production_preflight)
    return report


def _validate_aggregation(config: dict[str, Any], report: ValidationReport) -> None:
    aggregation = config.get("rules", {}).get("aggregation", {})
    if not isinstance(aggregation, dict):
        report.add_error("rules.aggregation", "must be an object")
        return
    for key in ["fail_blocks", "error_blocks", "manual_review_blocks", "skipped_blocks"]:
        value = aggregation.get(key)
        if value in UNRESOLVED_PLACEHOLDERS:
            continue
        if not isinstance(value, bool):
            report.add_error(f"rules.aggregation.{key}", "must be boolean")
    warning_status = aggregation.get("warning_overall_status")
    if warning_status in UNRESOLVED_PLACEHOLDERS:
        return
    if warning_status not in VALID_CHECK_STATUSES:
        report.add_error(
            "rules.aggregation.warning_overall_status",
            "invalid status enum",
        )


def _validate_parity(config: dict[str, Any], report: ValidationReport) -> None:
    parity = config.get("rules", {}).get("parity", [])
    if isinstance(parity, str) and parity in UNRESOLVED_PLACEHOLDERS:
        return
    if not isinstance(parity, list):
        report.add_error("rules.parity", "must be a list")
        return
    site_ids = _site_ids(config)
    for idx, rule in enumerate(parity):
        path = f"rules.parity[{idx}]"
        if not isinstance(rule, dict):
            report.add_error(path, "parity rule must be an object")
            continue
        enabled = rule.get("enabled")
        if enabled not in UNRESOLVED_PLACEHOLDERS and not isinstance(enabled, bool):
            report.add_error(f"{path}.enabled", "must be boolean")
        module = rule.get("module")
        if module not in (VALID_MODULES | UNRESOLVED_PLACEHOLDERS):
            report.add_error(f"{path}.module", "unknown module")
        fields = rule.get("fields")
        if fields is None and "field" in rule:
            fields = [rule["field"]]
        if isinstance(fields, str) and fields in UNRESOLVED_PLACEHOLDERS:
            continue
        if not isinstance(fields, list) or not fields:
            report.add_error(f"{path}.fields", "must be a non-empty list")
        else:
            for field_idx, field in enumerate(fields):
                if field in UNRESOLVED_PLACEHOLDERS:
                    continue
                if module == "portainer" and field not in PORTAINER_PARITY_FIELDS:
                    report.add_error(
                        f"{path}.fields[{field_idx}]",
                        "unknown Portainer parity field",
                    )
        sites = rule.get("sites")
        if not isinstance(sites, list) or len(sites) != 2:
            report.add_error(f"{path}.sites", "must contain exactly two sites")
        else:
            for site_idx, site in enumerate(sites):
                if site in UNRESOLVED_PLACEHOLDERS:
                    continue
                if site not in site_ids:
                    report.add_error(f"{path}.sites[{site_idx}]", "unknown site reference")
        mismatch_status = rule.get("mismatch_status")
        if isinstance(mismatch_status, str) and mismatch_status in UNRESOLVED_PLACEHOLDERS:
            pass
        elif mismatch_status not in VALID_CHECK_STATUSES:
            report.add_error(f"{path}.mismatch_status", "invalid status enum")
        allowed = rule.get("allowed_differences", [])
        if isinstance(allowed, str) and allowed in UNRESOLVED_PLACEHOLDERS:
            continue
        if not isinstance(allowed, list):
            report.add_error(f"{path}.allowed_differences", "must be a list")
            continue
        for allow_idx, item in enumerate(allowed):
            if not isinstance(item, dict):
                report.add_error(
                    f"{path}.allowed_differences[{allow_idx}]",
                    "allowed difference must be an object",
                )
                continue
            site_values = item.get("site_values")
            if isinstance(site_values, str) and site_values in UNRESOLVED_PLACEHOLDERS:
                continue
            if not isinstance(site_values, dict):
                report.add_error(
                    f"{path}.allowed_differences[{allow_idx}].site_values",
                    "must map site IDs to explicit expected parity values",
                )


def _validate_review_policy(config: dict[str, Any], report: ValidationReport) -> None:
    review = config.get("rules", {}).get("review", {})
    if not isinstance(review, dict):
        report.add_error("rules.review", "must be an object")
        return
    for key in [
        "general_notes_enabled",
        "general_note_required",
        "manual_review_acknowledgment_required",
        "recording_cleanup_acknowledgment_required",
        "reject_allowed",
    ]:
        value = review.get(key)
        if value is None or value in UNRESOLVED_PLACEHOLDERS:
            continue
        if not isinstance(value, bool):
            report.add_error(f"rules.review.{key}", "must be boolean")
    valid_review_modules = VALID_MODULES | {"site_parity"}
    required_modules = review.get("required_module_notes", [])
    if isinstance(required_modules, str) and required_modules in UNRESOLVED_PLACEHOLDERS:
        pass
    elif not isinstance(required_modules, list):
        report.add_error("rules.review.required_module_notes", "must be a list")
    else:
        for idx, module in enumerate(required_modules):
            if module not in valid_review_modules:
                report.add_error(
                    f"rules.review.required_module_notes[{idx}]",
                    "unknown review module",
                )
    required_statuses = review.get("result_note_required_statuses", [])
    if isinstance(required_statuses, str) and required_statuses in UNRESOLVED_PLACEHOLDERS:
        pass
    elif not isinstance(required_statuses, list):
        report.add_error("rules.review.result_note_required_statuses", "must be a list")
    else:
        for idx, status in enumerate(required_statuses):
            if status not in VALID_CHECK_STATUSES:
                report.add_error(
                    f"rules.review.result_note_required_statuses[{idx}]",
                    "invalid status enum",
                )
    approval = review.get("approval_status_policy", {})
    if isinstance(approval, str) and approval in UNRESOLVED_PLACEHOLDERS:
        return
    if not isinstance(approval, dict):
        report.add_error("rules.review.approval_status_policy", "must be an object")
        return
    valid_actions = {"ALLOW", "BLOCK", "REQUIRE_NOTE"}
    for status in VALID_CHECK_STATUSES - {"PASS"}:
        action = approval.get(status)
        if action is None or action in UNRESOLVED_PLACEHOLDERS:
            continue
        if action not in valid_actions:
            report.add_error(
                f"rules.review.approval_status_policy.{status}",
                "must be ALLOW, BLOCK, or REQUIRE_NOTE",
            )


def _validate_splunk(config: dict[str, Any], report: ValidationReport) -> None:
    dashboards = config.get("splunk_dashboards", {}).get("dashboards", [])
    if not isinstance(dashboards, list):
        report.add_error("splunk_dashboards.dashboards", "must be a list")
        return
    seen: set[str] = set()
    for idx, dashboard in enumerate(dashboards):
        if not isinstance(dashboard, dict):
            report.add_error(f"splunk_dashboards.dashboards[{idx}]", "dashboard must be object")
            continue
        dashboard_id = dashboard.get("id")
        if not dashboard_id:
            report.add_error(f"splunk_dashboards.dashboards[{idx}].id", "dashboard id required")
        elif dashboard_id in seen:
            report.add_error(
                f"splunk_dashboards.dashboards[{idx}].id",
                f"duplicate dashboard id {dashboard_id!r}",
            )
        else:
            seen.add(dashboard_id)


def _validate_portainer(
    config: dict[str, Any],
    report: ValidationReport,
    *,
    production_preflight: bool,
) -> None:
    portainer = config.get("portainer_expected", {})
    if not isinstance(portainer, dict):
        report.add_error("portainer_expected", "must be an object")
        return
    mode = portainer.get("collection_mode")
    if mode is None:
        mode = "fixture" if portainer.get("fixture_actual") is not None else "live"
    if mode not in PORTAINER_SUPPORTED_COLLECTION_MODES and mode not in UNRESOLVED_PLACEHOLDERS:
        report.add_error("portainer_expected.collection_mode", "must be fixture or live")
        return
    resolved_portainer = resolve_portainer_expected(config)
    sites = resolved_portainer.get("sites", {})
    if not isinstance(sites, dict) or not sites:
        report.add_error("portainer_expected.sites", "must define site mappings")
        return
    for site_id, site_config in sites.items():
        if not isinstance(site_config, dict):
            report.add_error(f"portainer_expected.sites.{site_id}", "site must be an object")
            continue
        environment_type = site_config.get("environment_type", "docker_swarm")
        if environment_type not in {"docker_swarm", *UNRESOLVED_PLACEHOLDERS}:
            report.add_error(
                f"portainer_expected.sites.{site_id}.environment_type",
                "Portainer scope is Docker Swarm only",
            )
        services = site_config.get("services")
        if not isinstance(services, list) or not services:
            report.add_error(
                f"portainer_expected.sites.{site_id}.services",
                "must resolve configured Swarm services from common inventory or site services",
            )
        else:
            for idx, service in enumerate(services):
                _validate_portainer_service(site_id, idx, service, report)
        if mode == "live":
            _validate_portainer_connection(
                site_id,
                site_config.get("connection"),
                report,
                production_preflight=production_preflight,
                portainer_enabled=_portainer_enabled(config),
            )
    if mode == "live" and portainer.get("fixture_actual") is not None:
        report.add_error(
            "portainer_expected.fixture_actual",
            "live Portainer mode must not include fixture fallback data",
        )


def _validate_portainer_service(
    site_id: str,
    idx: int,
    service: Any,
    report: ValidationReport,
) -> None:
    path = f"portainer_expected.sites.{site_id}.services[{idx}]"
    if not isinstance(service, dict):
        report.add_error(path, "service must be an object")
        return
    if "name" not in service:
        report.add_error(f"{path}.name", "is required")
    required = service.get("required", True)
    if required not in UNRESOLVED_PLACEHOLDERS and not isinstance(required, bool):
        report.add_error(f"{path}.required", "must be boolean when explicitly set")
    expected = service.get("expected")
    if expected is None:
        expected = {
            "desired_replicas": service.get("expected_replicas"),
            "running_replicas": service.get("expected_replicas"),
            "healthy_replicas": service.get("healthy_replicas_required"),
            "image": service.get("expected_image"),
            "image_comparison": service.get("image_comparison", "full_reference"),
        }
    if not isinstance(expected, dict):
        report.add_error(f"{path}.expected", "must be an object")
        return
    for field_name in ["desired_replicas", "running_replicas", "healthy_replicas"]:
        value = expected.get(field_name)
        if value in UNRESOLVED_PLACEHOLDERS:
            continue
        if not isinstance(value, int) or value < 0:
            report.add_error(f"{path}.expected.{field_name}", "must be non-negative integer")
    service_state = expected.get("service_state")
    if service_state is not None and service_state not in UNRESOLVED_PLACEHOLDERS:
        if not isinstance(service_state, str) or not service_state:
            report.add_error(f"{path}.expected.service_state", "must be non-empty string")
    task_policy = expected.get("task_state_policy", {})
    if isinstance(task_policy, str) and task_policy in UNRESOLVED_PLACEHOLDERS:
        pass
    elif not isinstance(task_policy, dict):
        report.add_error(f"{path}.expected.task_state_policy", "must be an object")
    else:
        for state, action in task_policy.items():
            if str(state) not in {"failed", "rejected", "restarting", "starting"}:
                report.add_error(
                    f"{path}.expected.task_state_policy.{state}",
                    "unsupported task state policy key",
                )
            if action in UNRESOLVED_PLACEHOLDERS:
                continue
            if action not in {"IGNORE", "WARNING", "FAIL", "ERROR"}:
                report.add_error(
                    f"{path}.expected.task_state_policy.{state}",
                    "must be IGNORE, WARNING, FAIL, or ERROR",
                )
    image = expected.get("image")
    comparison = expected.get("image_comparison", "full_reference")
    if isinstance(image, dict):
        comparison = image.get("comparison", comparison)
        if "reference" not in image and "value" not in image:
            report.add_error(f"{path}.expected.image.reference", "is required")
    if (
        comparison not in PORTAINER_SUPPORTED_IMAGE_COMPARISONS
        and comparison not in UNRESOLVED_PLACEHOLDERS
    ):
        report.add_error(
            f"{path}.expected.image_comparison",
            "must be full_reference, repository_tag, or digest",
        )


def _validate_portainer_connection(
    site_id: str,
    connection: Any,
    report: ValidationReport,
    *,
    production_preflight: bool,
    portainer_enabled: bool,
) -> None:
    path = f"portainer_expected.sites.{site_id}.connection"
    if not isinstance(connection, dict):
        report.add_error(path, "live mode requires a connection object")
        return
    _validate_env_reference(
        connection.get("url_env"),
        f"{path}.url_env",
        report,
        production_preflight=production_preflight and portainer_enabled,
        label="Portainer URL",
    )
    endpoint_id = connection.get("endpoint_id")
    if (
        not isinstance(endpoint_id, str)
        or not endpoint_id
        or endpoint_id in UNRESOLVED_PLACEHOLDERS
    ):
        report.add_error(f"{path}.endpoint_id", "live mode requires a resolved endpoint ID")
    api_contract = connection.get("api_contract")
    if api_contract in UNRESOLVED_PLACEHOLDERS:
        pass
    elif api_contract not in PORTAINER_SUPPORTED_API_CONTRACTS:
        report.add_error(f"{path}.api_contract", "unsupported or unverified API contract")
    auth = connection.get("auth", {})
    if not isinstance(auth, dict):
        report.add_error(f"{path}.auth", "must be an object")
        return
    auth_type = auth.get("type")
    if auth_type in UNRESOLVED_PLACEHOLDERS:
        pass
    elif auth_type not in PORTAINER_SUPPORTED_AUTH_TYPES:
        report.add_error(f"{path}.auth.type", "unsupported authentication type")
    if auth_type in {"bearer_token", "jwt", "x_api_key"}:
        _validate_env_reference(
            auth.get("token_env"),
            f"{path}.auth.token_env",
            report,
            production_preflight=production_preflight and portainer_enabled,
            label="Portainer token",
        )
    tls = connection.get("tls", {})
    if not isinstance(tls, dict):
        report.add_error(f"{path}.tls", "must be an object")
    else:
        verify = tls.get("verify")
        if verify is False:
            report.add_error(f"{path}.tls.verify", "verify=false is not allowed for live mode")
        elif verify is True or verify in UNRESOLVED_PLACEHOLDERS:
            pass
        elif verify == "custom_ca":
            _validate_env_reference(
                tls.get("ca_file_env"),
                f"{path}.tls.ca_file_env",
                report,
                production_preflight=production_preflight and portainer_enabled,
                label="Portainer CA file",
            )
        else:
            report.add_error(f"{path}.tls.verify", "must be true or custom_ca")
    timeouts = connection.get("timeouts", {})
    retries = connection.get("retries", {})
    _validate_number(
        timeouts.get("connect_seconds"),
        f"{path}.timeouts.connect_seconds",
        report,
    )
    _validate_number(
        timeouts.get("read_seconds"),
        f"{path}.timeouts.read_seconds",
        report,
    )
    _validate_integer(
        retries.get("attempts"),
        f"{path}.retries.attempts",
        report,
    )
    _validate_number(
        retries.get("backoff_seconds"),
        f"{path}.retries.backoff_seconds",
        report,
    )


def _validate_rabbitmq(
    config: dict[str, Any],
    report: ValidationReport,
    *,
    production_preflight: bool,
) -> None:
    rabbitmq = config.get("rabbitmq_expected", {})
    if not isinstance(rabbitmq, dict):
        report.add_error("rabbitmq_expected", "must be an object")
        return
    mode = rabbitmq.get("collection_mode")
    if mode is None:
        mode = "fixture" if rabbitmq.get("fixture_actual") is not None else "live"
    if mode not in RABBITMQ_SUPPORTED_COLLECTION_MODES and mode not in UNRESOLVED_PLACEHOLDERS:
        report.add_error("rabbitmq_expected.collection_mode", "must be fixture or live")
        return
    resolved_rabbitmq = resolve_rabbitmq_expected(config)
    sites = resolved_rabbitmq.get("sites", {})
    if not isinstance(sites, dict) or not sites:
        report.add_error("rabbitmq_expected.sites", "must define site topology mappings")
        return
    for site_id, site_config in sites.items():
        if not isinstance(site_config, dict):
            report.add_error(f"rabbitmq_expected.sites.{site_id}", "site must be an object")
            continue
        for idx, vhost in enumerate(site_config.get("vhosts", [])):
            _validate_required_name(
                vhost,
                f"rabbitmq_expected.sites.{site_id}.vhosts[{idx}]",
                report,
            )
        for idx, queue in enumerate(site_config.get("queues", [])):
            _validate_rabbitmq_queue(
                queue,
                f"rabbitmq_expected.sites.{site_id}.queues[{idx}]",
                report,
            )
        for idx, exchange in enumerate(site_config.get("exchanges", [])):
            _validate_rabbitmq_exchange(
                exchange,
                f"rabbitmq_expected.sites.{site_id}.exchanges[{idx}]",
                report,
            )
        for idx, binding in enumerate(site_config.get("bindings", [])):
            _validate_rabbitmq_binding(
                binding,
                f"rabbitmq_expected.sites.{site_id}.bindings[{idx}]",
                report,
            )
    if mode == "live":
        connections = rabbitmq.get("connections", {})
        if not isinstance(connections, dict):
            report.add_error("rabbitmq_expected.connections", "live mode requires connections")
        else:
            for site_id in sites:
                _validate_rabbitmq_connection(
                    site_id,
                    connections.get(site_id),
                    report,
                    production_preflight=production_preflight and _rabbitmq_enabled(config),
                )
        if rabbitmq.get("fixture_actual") is not None:
            report.add_error(
                "rabbitmq_expected.fixture_actual",
                "live RabbitMQ mode must not include fixture fallback data",
            )


def _validate_required_name(value: Any, path: str, report: ValidationReport) -> None:
    if not isinstance(value, dict):
        report.add_error(path, "must be an object")
        return
    name = value.get("name")
    if name in UNRESOLVED_PLACEHOLDERS:
        return
    if not isinstance(name, str) or not name:
        report.add_error(f"{path}.name", "is required")
    required = value.get("required", True)
    if required not in UNRESOLVED_PLACEHOLDERS and not isinstance(required, bool):
        report.add_error(f"{path}.required", "must be boolean when explicitly set")


def _validate_rabbitmq_queue(value: Any, path: str, report: ValidationReport) -> None:
    _validate_required_name(value, path, report)
    if not isinstance(value, dict):
        return
    for field_name in ["vhost"]:
        field_value = value.get(field_name)
        if field_value in UNRESOLVED_PLACEHOLDERS:
            continue
        if not isinstance(field_value, str) or not field_value:
            report.add_error(f"{path}.{field_name}", "is required")
    for field_name in ["durable", "auto_delete", "exclusive"]:
        field_value = value.get(field_name)
        if field_value in UNRESOLVED_PLACEHOLDERS:
            continue
        if not isinstance(field_value, bool):
            report.add_error(f"{path}.{field_name}", "must be boolean")
    _validate_integer(value.get("min_consumers"), f"{path}.min_consumers", report)
    _validate_number(value.get("warning_messages"), f"{path}.warning_messages", report)
    _validate_number(value.get("critical_messages"), f"{path}.critical_messages", report)


def _validate_rabbitmq_exchange(value: Any, path: str, report: ValidationReport) -> None:
    _validate_required_name(value, path, report)
    if not isinstance(value, dict):
        return
    for field_name in ["vhost", "type"]:
        field_value = value.get(field_name)
        if field_value in UNRESOLVED_PLACEHOLDERS:
            continue
        if not isinstance(field_value, str) or not field_value:
            report.add_error(f"{path}.{field_name}", "is required")
    for field_name in ["durable", "auto_delete"]:
        field_value = value.get(field_name)
        if field_value in UNRESOLVED_PLACEHOLDERS:
            continue
        if not isinstance(field_value, bool):
            report.add_error(f"{path}.{field_name}", "must be boolean")


def _validate_rabbitmq_binding(value: Any, path: str, report: ValidationReport) -> None:
    if not isinstance(value, dict):
        report.add_error(path, "must be an object")
        return
    for field_name in ["vhost", "source", "destination", "routing_key"]:
        field_value = value.get(field_name)
        if field_value in UNRESOLVED_PLACEHOLDERS:
            continue
        if not isinstance(field_value, str) or not field_value:
            report.add_error(f"{path}.{field_name}", "is required")
    destination_type = value.get("destination_type")
    if destination_type in UNRESOLVED_PLACEHOLDERS:
        pass
    elif destination_type not in RABBITMQ_SUPPORTED_DESTINATION_TYPES:
        report.add_error(f"{path}.destination_type", "must be queue or exchange")
    required = value.get("required", True)
    if required not in UNRESOLVED_PLACEHOLDERS and not isinstance(required, bool):
        report.add_error(f"{path}.required", "must be boolean when explicitly set")


def _validate_rabbitmq_connection(
    site_id: str,
    connection: Any,
    report: ValidationReport,
    *,
    production_preflight: bool,
) -> None:
    path = f"rabbitmq_expected.connections.{site_id}"
    if not isinstance(connection, dict):
        report.add_error(path, "live mode requires a connection object")
        return
    _validate_env_reference(
        connection.get("url_env"),
        f"{path}.url_env",
        report,
        production_preflight=production_preflight,
        label="RabbitMQ URL",
    )
    _validate_env_reference(
        connection.get("user_env"),
        f"{path}.user_env",
        report,
        production_preflight=production_preflight,
        label="RabbitMQ user",
    )
    _validate_env_reference(
        connection.get("password_env"),
        f"{path}.password_env",
        report,
        production_preflight=production_preflight,
        label="RabbitMQ password",
    )
    tls_verify = connection.get("tls_verify")
    if tls_verify not in UNRESOLVED_PLACEHOLDERS and not isinstance(tls_verify, bool):
        report.add_error(f"{path}.tls_verify", "must be boolean")
    _validate_number(connection.get("timeout"), f"{path}.timeout", report)
    retry = connection.get("retry")
    if isinstance(retry, dict):
        _validate_integer(retry.get("attempts"), f"{path}.retry.attempts", report)
        _validate_number(retry.get("backoff_seconds"), f"{path}.retry.backoff_seconds", report)
    else:
        _validate_integer(retry, f"{path}.retry", report)


def _validate_database(
    config: dict[str, Any],
    report: ValidationReport,
    *,
    production_preflight: bool,
) -> None:
    database = config.get("database", {})
    if not isinstance(database, dict):
        report.add_error("database", "must be an object")
        return
    mode = database.get("collection_mode")
    if mode is None:
        mode = "fixture" if database.get("fixture_actual") is not None else "live"
    if mode not in DATABASE_SUPPORTED_COLLECTION_MODES and mode not in UNRESOLVED_PLACEHOLDERS:
        report.add_error("database.collection_mode", "must be fixture or live")
        return
    if mode == "fixture":
        fixture = database.get("fixture_actual")
        if not isinstance(fixture, dict):
            report.add_error("database.fixture_actual", "fixture mode requires fixture_actual")
        return
    if database.get("fixture_actual") is not None:
        report.add_error(
            "database.fixture_actual",
            "live Database mode must not include fixture fallback data",
        )
    adapter = database.get("adapter")
    if isinstance(adapter, str) and adapter in UNRESOLVED_PLACEHOLDERS:
        report.add_error("database.adapter", "database adapter is unresolved")
    elif adapter != "existing_sync_function":
        report.add_error("database.adapter", "must be existing_sync_function")
    function_reference = database.get("function_reference")
    if (
        isinstance(function_reference, str)
        and function_reference in UNRESOLVED_PLACEHOLDERS
    ) or not isinstance(function_reference, str):
        report.add_error(
            "database.function_reference",
            "approved existing sync function reference is required",
        )
    secret_env = database.get("required_secret_env", [])
    if isinstance(secret_env, str) and secret_env in UNRESOLVED_PLACEHOLDERS:
        report.add_error("database.required_secret_env", "database secret list is unresolved")
        return
    if not isinstance(secret_env, list):
        report.add_error("database.required_secret_env", "must be a list")
        return
    for idx, env_name in enumerate(secret_env):
        _validate_env_reference(
            env_name,
            f"database.required_secret_env[{idx}]",
            report,
            production_preflight=production_preflight and _database_enabled(config),
            label="Database secret",
        )


def _validate_env_reference(
    env_name: Any,
    path: str,
    report: ValidationReport,
    *,
    production_preflight: bool,
    label: str,
) -> None:
    if not isinstance(env_name, str) or not env_name or env_name in UNRESOLVED_PLACEHOLDERS:
        report.add_error(path, f"{label} environment variable name is required")
        return
    if production_preflight and _runtime_unset(os.getenv(env_name)):
        report.add_error(path, f"{label} runtime value is missing or unresolved: {env_name}")


def _validate_number(value: Any, path: str, report: ValidationReport) -> None:
    if value in UNRESOLVED_PLACEHOLDERS:
        return
    if not isinstance(value, int | float) or value < 0:
        report.add_error(path, "must be a non-negative number")


def _validate_integer(value: Any, path: str, report: ValidationReport) -> None:
    if value in UNRESOLVED_PLACEHOLDERS:
        return
    if not isinstance(value, int) or value < 0:
        report.add_error(path, "must be a non-negative integer")


def _portainer_enabled(config: dict[str, Any]) -> bool:
    module = _module_rules(config).get("portainer", {})
    return bool(module.get("enabled") and module.get("required"))


def _rabbitmq_enabled(config: dict[str, Any]) -> bool:
    module = _module_rules(config).get("rabbitmq", {})
    return bool(module.get("enabled") and module.get("required"))


def _database_enabled(config: dict[str, Any]) -> bool:
    module = _module_rules(config).get("database", {})
    return bool(module.get("enabled") and module.get("required"))


def _validate_thresholds(config: dict[str, Any], report: ValidationReport) -> None:
    for path, value in iter_leaves(config):
        if path.endswith("critical_messages"):
            warning_path = path.removesuffix("critical_messages") + "warning_messages"
            warning = _get_path(config, warning_path)
            if isinstance(value, int | float) and isinstance(warning, int | float):
                if value <= warning:
                    report.add_error(
                        path, "critical threshold must be greater than warning threshold"
                    )
        if path.endswith("critical_percent"):
            warning_path = path.removesuffix("critical_percent") + "warning_percent"
            warning = _get_path(config, warning_path)
            if isinstance(value, int | float) and isinstance(warning, int | float):
                if value <= warning:
                    report.add_error(
                        path, "critical threshold must be greater than warning threshold"
                    )


def _get_path(config: dict[str, Any], dotted: str) -> Any:
    current: Any = config
    normalized = dotted.replace("[", ".").replace("]", "")
    for part in normalized.split("."):
        if part == "":
            continue
        if part.isdigit() and isinstance(current, list):
            idx = int(part)
            if idx >= len(current):
                return None
            current = current[idx]
        elif isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def _validate_runtime_environment(
    config: dict[str, Any],
    report: ValidationReport,
    *,
    production_preflight: bool,
) -> None:
    for message in runtime_identity_errors(config, production_preflight=production_preflight):
        report.add_error("runtime.traceability", message)
    if not production_preflight:
        return
    mode = os.getenv("WEEKEND_REPORT_AUTH_MODE", "development").strip().lower()
    if mode != "production":
        return
    provider = os.getenv("WEEKEND_REPORT_AUTH_PROVIDER", "").strip()
    if _runtime_unset(provider):
        report.add_error(
            "runtime.auth",
            "WEEKEND_REPORT_AUTH_PROVIDER must be set in production auth mode",
        )
    elif provider == "trusted_header" and _runtime_unset(
        os.getenv("WEEKEND_REPORT_AUTH_TRUSTED_HEADER")
    ):
        report.add_error(
            "runtime.auth",
            "WEEKEND_REPORT_AUTH_TRUSTED_HEADER must be set for trusted_header auth",
        )
    if _runtime_unset(os.getenv("WEEKEND_REPORT_AUTHORIZED_REVIEWERS")):
        report.add_error(
            "runtime.auth",
            "WEEKEND_REPORT_AUTHORIZED_REVIEWERS must be set in production auth mode",
        )
    if _runtime_unset(os.getenv("WEEKEND_REPORT_CSRF_SIGNING_KEY")):
        report.add_error(
            "runtime.csrf",
            "WEEKEND_REPORT_CSRF_SIGNING_KEY must be set for production browser mutations",
        )


def _runtime_unset(value: str | None) -> bool:
    return value is None or value.strip() in UNSET_RUNTIME_VALUES


def _validate_placeholders(
    config: dict[str, Any],
    report: ValidationReport,
    *,
    production_preflight: bool,
) -> None:
    unresolved_is_blocking = production_preflight and _has_enabled_required_automation(config)
    for path, value in iter_leaves(config):
        if value not in ALL_PLACEHOLDERS:
            continue
        if value in UNRESOLVED_PLACEHOLDERS:
            message = f"unresolved value {value}"
            if unresolved_is_blocking:
                report.add_error(path, message)
            else:
                report.add_warning(path, message)
        elif value == PLACEHOLDER_NOT_APPLICABLE:
            if not any(part in path for part in NOT_APPLICABLE_ALLOWED_PATH_PARTS):
                report.add_error(path, "<NOT_APPLICABLE> is not permitted for this field")
