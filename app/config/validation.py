from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import PureWindowsPath
from typing import Any

from app.config.schema import (
    DOCTOR_MANUAL_REVIEW_TRIGGERS,
    DOCTOR_SUPPORTED_MODES,
    NOT_APPLICABLE_ALLOWED_PATH_PARTS,
    PLACEHOLDER_NOT_APPLICABLE,
    RABBITMQ_HEALTH_STATES,
    RABBITMQ_SUPPORTED_COLLECTION_MODES,
    RABBITMQ_SUPPORTED_SCOPES,
    RECORDING_INITIAL_STATES,
    RECORDING_SUPPORTED_COLLECTION_MODES,
    RECORDING_SUPPORTED_WORKFLOWS,
    SSH_SUPPORTED_AUTH_TYPES,
    SSH_SUPPORTED_HOST_KEY_POLICIES,
    UNRESOLVED_PLACEHOLDERS,
    VALID_CHECK_STATUSES,
    VALID_MODULES,
    is_unresolved_placeholder,
)
from app.runtime_identity import runtime_identity_errors

UNSET_RUNTIME_VALUES = {"", "<TBD>", "<TO_VERIFY>", "UNKNOWN"}
PORTAINER_SUPPORTED_COLLECTION_MODES = {"fixture", "live"}
PORTAINER_SUPPORTED_AUTH_TYPES = {"bearer_token", "jwt", "x_api_key", "none"}
PORTAINER_SUPPORTED_API_CONTRACTS = {"docker_proxy_v1"}
DOCTOR_PARITY_FIELDS = {"service_presence"}
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
    _validate_manual_db_check(config, report)
    site_ids = _site_ids(config)
    for area in ["portainer_expected", "rabbitmq_expected", "recording", "servers"]:
        by_site = config.get(area, {}).get("sites")
        if isinstance(by_site, dict):
            for site_id in by_site:
                if site_id not in site_ids:
                    report.add_error(f"{area}.sites.{site_id}", "unknown site reference")

    _validate_splunk(config, report)
    _validate_portainer(config, report, production_preflight=production_preflight)
    _validate_doctor(config, report)
    _validate_rabbitmq(config, report, production_preflight=production_preflight)
    _validate_recording(config, report, production_preflight=production_preflight)
    _validate_servers(config, report, production_preflight=production_preflight)
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
                if module == "doctor" and field not in DOCTOR_PARITY_FIELDS:
                    report.add_error(
                        f"{path}.fields[{field_idx}]",
                        "unknown DOCTOR parity field",
                    )
        if "sites" in rule:
            report.add_error(
                f"{path}.sites",
                "must not be configured in rules; parity uses runtime SITE*_ID values",
            )
        if len(site_ids) != 2:
            report.add_error(
                path,
                "enabled parity requires exactly two runtime sites from SITE_COUNT/SITE*_ID",
            )
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
    splunk = config.get("splunk_dashboards", {})
    if not isinstance(splunk, dict):
        report.add_error("splunk_dashboards", "must be an object")
        return

    dashboards = splunk.get("dashboards", [])
    if not isinstance(dashboards, list) or not dashboards:
        report.add_error("splunk_dashboards.dashboards", "must be a non-empty list")
        return

    seen_ids: set[str] = set()
    seen_orders: set[int] = set()
    for idx, dashboard in enumerate(dashboards):
        path = f"splunk_dashboards.dashboards[{idx}]"
        if not isinstance(dashboard, dict):
            report.add_error(path, "dashboard must be an object")
            continue

        for field_name in ["id", "display_name", "url"]:
            value = dashboard.get(field_name)
            if is_unresolved_placeholder(value):
                continue
            if not isinstance(value, str) or not value.strip():
                report.add_error(f"{path}.{field_name}", "must be a non-empty string")

        dashboard_id = dashboard.get("id")
        if isinstance(dashboard_id, str) and not is_unresolved_placeholder(dashboard_id):
            if dashboard_id in seen_ids:
                report.add_error(f"{path}.id", f"duplicate dashboard id {dashboard_id!r}")
            seen_ids.add(dashboard_id)

        for field_name in ["required_review", "note_required"]:
            value = dashboard.get(field_name)
            if is_unresolved_placeholder(value):
                continue
            if not isinstance(value, bool):
                report.add_error(f"{path}.{field_name}", "must be boolean")

        order = dashboard.get("order")
        if not is_unresolved_placeholder(order):
            if not isinstance(order, int) or isinstance(order, bool) or order <= 0:
                report.add_error(f"{path}.order", "must be a positive integer")
            elif order in seen_orders:
                report.add_error(f"{path}.order", f"duplicate dashboard order {order}")
            else:
                seen_orders.add(order)

    open_all = splunk.get("open_all", {})
    if not isinstance(open_all, dict):
        report.add_error("splunk_dashboards.open_all", "must be an object")
    else:
        include_optional = open_all.get("include_optional")
        if not is_unresolved_placeholder(include_optional) and not isinstance(
            include_optional, bool
        ):
            report.add_error("splunk_dashboards.open_all.include_optional", "must be boolean")


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
    for obsolete_key in ["defaults", "services"]:
        if obsolete_key in portainer:
            report.add_error(
                f"portainer_expected.{obsolete_key}",
                "configured expected Portainer services were removed; "
                "services are discovered from each site at runtime",
            )
    mode = portainer.get("collection_mode")
    if mode is None:
        mode = "fixture" if portainer.get("fixture_actual") is not None else "live"
    if mode not in PORTAINER_SUPPORTED_COLLECTION_MODES and mode not in UNRESOLVED_PLACEHOLDERS:
        report.add_error("portainer_expected.collection_mode", "must be fixture or live")
        return
    if mode == "fixture":
        if not isinstance(portainer.get("fixture_actual"), dict):
            report.add_error(
                "portainer_expected.fixture_actual",
                "fixture mode requires fixture_actual",
            )
        return
    sites = portainer.get("sites", {})
    if not isinstance(sites, dict) or not sites:
        report.add_error("portainer_expected.sites", "must define site mappings")
        return
    expected_site_ids = _site_ids(config)
    for expected_site_id in expected_site_ids:
        if expected_site_id not in sites:
            report.add_error(
                f"portainer_expected.sites.{expected_site_id}",
                "required site mapping is missing",
            )
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
    _validate_portainer_policy(config, report)


def _validate_portainer_policy(config: dict[str, Any], report: ValidationReport) -> None:
    policy = config.get("rules", {}).get("portainer", {})
    if not isinstance(policy, dict):
        report.add_error("rules.portainer", "must be an object")
        return
    task_policy = policy.get("task_state_policy", {})
    if not isinstance(task_policy, dict):
        report.add_error("rules.portainer.task_state_policy", "must be an object")
    else:
        for state, action in task_policy.items():
            if str(state) not in {"failed", "rejected", "restarting", "starting"}:
                report.add_error(
                    f"rules.portainer.task_state_policy.{state}",
                    "unsupported task state policy key",
                )
            if action not in {"IGNORE", "WARNING", "FAIL", "ERROR"}:
                report.add_error(
                    f"rules.portainer.task_state_policy.{state}",
                    "must be IGNORE, WARNING, FAIL, or ERROR",
                )
    state_policy = policy.get("service_state_policy", {})
    if not isinstance(state_policy, dict):
        report.add_error("rules.portainer.service_state_policy", "must be an object")
    else:
        for state, action in state_policy.items():
            if not isinstance(state, str) or not state.strip():
                report.add_error("rules.portainer.service_state_policy", "state must be non-empty")
            if action not in {"PASS", "WARNING", "FAIL", "ERROR"}:
                report.add_error(
                    f"rules.portainer.service_state_policy.{state}",
                    "must be PASS, WARNING, FAIL, or ERROR",
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
    _validate_runtime_value(
        connection.get("url"),
        f"{path}.url",
        report,
        required=production_preflight and portainer_enabled,
        label="Portainer URL",
    )
    endpoint_id = connection.get("endpoint_id")
    if is_unresolved_placeholder(endpoint_id):
        pass
    elif not isinstance(endpoint_id, str) or not endpoint_id.strip():
        report.add_error(
            f"{path}.endpoint_id",
            "live mode requires a non-empty endpoint ID",
        )
    api_contract = connection.get("api_contract")
    if is_unresolved_placeholder(api_contract):
        pass
    elif api_contract not in PORTAINER_SUPPORTED_API_CONTRACTS:
        report.add_error(
            f"{path}.api_contract",
            "unsupported or unverified API contract",
        )
    auth = connection.get("auth", {})
    if not isinstance(auth, dict):
        report.add_error(f"{path}.auth", "must be an object")
        return
    auth_type = auth.get("type")
    if is_unresolved_placeholder(auth_type):
        pass
    elif auth_type not in PORTAINER_SUPPORTED_AUTH_TYPES:
        report.add_error(
            f"{path}.auth.type",
            "unsupported authentication type",
        )
    if auth_type in {"bearer_token", "jwt", "x_api_key"}:
        _validate_runtime_value(
            auth.get("token"),
            f"{path}.auth.token",
            report,
            required=production_preflight and portainer_enabled,
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
            _validate_runtime_value(
                tls.get("ca_file"),
                f"{path}.tls.ca_file",
                report,
                required=production_preflight and portainer_enabled,
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


def _validate_doctor(config: dict[str, Any], report: ValidationReport) -> None:
    doctor = config.get("doctor", {}).get("doctor")
    if not isinstance(doctor, dict):
        report.add_error("doctor.doctor", "must be an object")
        return

    mode = doctor.get("mode")
    if is_unresolved_placeholder(mode):
        return
    if mode not in DOCTOR_SUPPORTED_MODES:
        report.add_error("doctor.doctor.mode", "must be api or manual")
        return

    manual = doctor.get("manual_review", {})
    if not isinstance(manual, dict):
        report.add_error("doctor.doctor.manual_review", "must be an object")
        manual = {}

    if mode == "manual":
        for field_name in ["url", "instructions"]:
            value = manual.get(field_name)
            if is_unresolved_placeholder(value):
                continue
            if not isinstance(value, str) or not value.strip():
                report.add_error(
                    f"doctor.doctor.manual_review.{field_name}",
                    "must be a non-empty string in manual mode",
                )
        note_required = manual.get("note_required")
        if not is_unresolved_placeholder(note_required) and not isinstance(note_required, bool):
            report.add_error("doctor.doctor.manual_review.note_required", "must be boolean")
        return

    api = doctor.get("api")
    if not isinstance(api, dict):
        report.add_error("doctor.doctor.api", "api mode requires an api object")
    else:
        for field_name in ["site1_url", "site2_url"]:
            value = api.get(field_name)
            if is_unresolved_placeholder(value):
                continue
            if not isinstance(value, str) or not value.strip():
                report.add_error(f"doctor.doctor.api.{field_name}", "must be a non-empty string")

    validation = doctor.get("validation")
    if not isinstance(validation, dict):
        report.add_error(
            "doctor.doctor.validation",
            "api mode requires a validation object",
        )
    else:
        expected_statuses = {
            "healthy_status": "PASS",
            "unhealthy_status": "ERROR",
            "all_healthy_status": "PASS",
            "any_unhealthy_status": "MANUAL_REVIEW",
        }
        for field_name, expected_status in expected_statuses.items():
            status = validation.get(field_name)
            if is_unresolved_placeholder(status):
                continue
            if status != expected_status:
                report.add_error(
                    f"doctor.doctor.validation.{field_name}",
                    f"must be {expected_status}",
                )
        reason_required = validation.get("unhealthy_reason_required")
        if not is_unresolved_placeholder(reason_required) and reason_required is not True:
            report.add_error(
                "doctor.doctor.validation.unhealthy_reason_required",
                "must be true",
            )

    required_when = manual.get("required_when")
    if (
        not is_unresolved_placeholder(required_when)
        and required_when not in DOCTOR_MANUAL_REVIEW_TRIGGERS
    ):
        report.add_error(
            "doctor.doctor.manual_review.required_when",
            "api mode requires required_when=any_unhealthy",
        )
    note_required = manual.get("note_required")
    if not is_unresolved_placeholder(note_required) and note_required is not True:
        report.add_error(
            "doctor.doctor.manual_review.note_required",
            "must be true",
        )
    instructions = manual.get("instructions")
    if not is_unresolved_placeholder(instructions) and (
        not isinstance(instructions, str) or not instructions.strip()
    ):
        report.add_error("doctor.doctor.manual_review.instructions", "must be a non-empty string")


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
    if is_unresolved_placeholder(mode):
        return
    if mode not in RABBITMQ_SUPPORTED_COLLECTION_MODES:
        report.add_error("rabbitmq_expected.collection_mode", "must be fixture or live")
        return

    if mode == "fixture":
        if not isinstance(
            rabbitmq.get("fixture_actual"),
            dict,
        ):
            report.add_error(
                "rabbitmq_expected.fixture_actual",
                "fixture mode requires fixture_actual",
            )

    if mode == "live" and rabbitmq.get("fixture_actual") is not None:
        report.add_error(
            "rabbitmq_expected.fixture_actual",
            "live RabbitMQ mode must not include fixture fallback data",
        )

    for legacy_key in ["defaults", "topology"]:
        if legacy_key in rabbitmq:
            report.add_error(
                f"rabbitmq_expected.{legacy_key}",
                "legacy RabbitMQ topology validation was removed",
            )

    sites = rabbitmq.get("sites")
    if not isinstance(sites, dict) or not sites:
        report.add_error("rabbitmq_expected.sites", "must define site mappings")
        sites = {}
    else:
        expected_site_ids = _site_ids(config)
        for site_id in expected_site_ids:
            if site_id not in sites:
                report.add_error(
                    f"rabbitmq_expected.sites.{site_id}", "required site mapping is missing"
                )
        for site_id, site in sites.items():
            path = f"rabbitmq_expected.sites.{site_id}"
            if not isinstance(site, dict):
                report.add_error(path, "site must be an object")
                continue
            required = site.get("required")
            if not is_unresolved_placeholder(required) and not isinstance(required, bool):
                report.add_error(f"{path}.required", "must be boolean")

    connections = rabbitmq.get("connections")
    if mode == "live":
        if not isinstance(connections, dict):
            report.add_error(
                "rabbitmq_expected.connections",
                "live mode requires connections",
            )
        else:
            for site_id in sites:
                _validate_rabbitmq_connection(
                    site_id,
                    connections.get(site_id),
                    report,
                    production_preflight=(production_preflight and _rabbitmq_enabled(config)),
                )

    queues = rabbitmq.get("queues")
    if not isinstance(queues, dict):
        report.add_error("rabbitmq_expected.queues", "must be an object")
    else:
        scope = queues.get("scope")
        if not is_unresolved_placeholder(scope) and scope not in RABBITMQ_SUPPORTED_SCOPES:
            report.add_error("rabbitmq_expected.queues.scope", "must be all")

        expected = queues.get("expected")
        if not isinstance(expected, dict):
            report.add_error("rabbitmq_expected.queues.expected", "must be an object")
        else:
            for field_name in ["ready", "unacked", "total"]:
                value = expected.get(field_name)
                path = f"rabbitmq_expected.queues.expected.{field_name}"
                if is_unresolved_placeholder(value):
                    continue
                if not isinstance(value, int) or isinstance(value, bool) or value != 0:
                    report.add_error(path, "must be integer 0 for the Weekend Report queue rule")

        recheck = queues.get("recheck")
        if not isinstance(recheck, dict):
            report.add_error("rabbitmq_expected.queues.recheck", "must be an object")
        else:
            _validate_positive_integer(
                recheck.get("refresh_attempts"),
                "rabbitmq_expected.queues.recheck.refresh_attempts",
                report,
            )
            _validate_number(
                recheck.get("delay_seconds"),
                "rabbitmq_expected.queues.recheck.delay_seconds",
                report,
            )

        status = queues.get("nonzero_after_rechecks_status")
        if not is_unresolved_placeholder(status) and status != "ERROR":
            report.add_error(
                "rabbitmq_expected.queues.nonzero_after_rechecks_status",
                "must be ERROR",
            )

    nodes = rabbitmq.get("nodes")
    if not isinstance(nodes, dict):
        report.add_error("rabbitmq_expected.nodes", "must be an object")
    else:
        scope = nodes.get("scope")
        if not is_unresolved_placeholder(scope) and scope not in RABBITMQ_SUPPORTED_SCOPES:
            report.add_error("rabbitmq_expected.nodes.scope", "must be all")
        expected = nodes.get("expected")
        if not isinstance(expected, dict):
            report.add_error("rabbitmq_expected.nodes.expected", "must be an object")
        else:
            for field_name in [
                "file_descriptors",
                "socket_descriptors",
                "erlang_processes",
                "disk_space",
            ]:
                value = expected.get(field_name)
                if is_unresolved_placeholder(value):
                    continue
                if value not in RABBITMQ_HEALTH_STATES:
                    report.add_error(
                        f"rabbitmq_expected.nodes.expected.{field_name}",
                        "must be green",
                    )
            status = nodes.get("unhealthy_status")
            if not is_unresolved_placeholder(status) and status != "ERROR":
                report.add_error(
                    "rabbitmq_expected.nodes.unhealthy_status",
                    "must be ERROR",
                )


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
    _validate_runtime_value(
        connection.get("url"),
        f"{path}.url",
        report,
        required=production_preflight,
        label="RabbitMQ URL",
    )
    _validate_runtime_value(
        connection.get("user"),
        f"{path}.user",
        report,
        required=production_preflight,
        label="RabbitMQ user",
    )
    _validate_runtime_value(
        connection.get("password"),
        f"{path}.password",
        report,
        required=production_preflight,
        label="RabbitMQ password",
    )
    tls_verify = connection.get("tls_verify")
    if is_unresolved_placeholder(tls_verify):
        pass
    elif tls_verify is True:
        pass
    elif tls_verify == "custom_ca":
        _validate_runtime_value(
            connection.get("ca_file"),
            f"{path}.ca_file",
            report,
            required=production_preflight,
            label="RabbitMQ CA file",
        )
    elif tls_verify is False:
        report.add_error(
            f"{path}.tls_verify",
            "verify=false is not allowed for live mode",
        )
    else:
        report.add_error(
            f"{path}.tls_verify",
            "must be true or custom_ca",
        )
    _validate_positive_number(connection.get("timeout_seconds"), f"{path}.timeout_seconds", report)
    _validate_positive_integer(connection.get("retry_attempts"), f"{path}.retry_attempts", report)


def _validate_recording(
    config: dict[str, Any],
    report: ValidationReport,
    *,
    production_preflight: bool,
) -> None:
    recording = config.get("recording", {})
    if not isinstance(recording, dict):
        report.add_error("recording", "must be an object")
        return

    mode = recording.get("collection_mode")
    if mode is None:
        mode = "fixture" if recording.get("fixture_actual") is not None else "live"
    if is_unresolved_placeholder(mode):
        return
    if mode not in RECORDING_SUPPORTED_COLLECTION_MODES:
        report.add_error("recording.collection_mode", "must be fixture or live")
        return

    workflow = recording.get("workflow")
    if not is_unresolved_placeholder(workflow) and workflow not in RECORDING_SUPPORTED_WORKFLOWS:
        report.add_error(
            "recording.workflow",
            "must be existing_device_start_stop",
        )

    safety = recording.get("safety")
    if not isinstance(safety, dict):
        report.add_error("recording.safety", "must be an object")
        safety = {}

    safety_contract = {
        "create_device_allowed": False,
        "delete_device_allowed": False,
        "state_changing_calls_require_explicit_approval": True,
        "crash_after_start_requires_recovery": True,
        "automatic_replay_after_unknown_state": False,
    }
    for key, expected in safety_contract.items():
        value = safety.get(key)
        if is_unresolved_placeholder(value):
            continue
        if not isinstance(value, bool):
            report.add_error(
                f"recording.safety.{key}",
                "must be boolean",
            )
        elif value is not expected:
            report.add_error(
                f"recording.safety.{key}",
                f"must be {str(expected).lower()} for the approved safety contract",
            )

    if mode == "fixture":
        if not isinstance(recording.get("fixture_actual"), dict):
            report.add_error(
                "recording.fixture_actual",
                "fixture mode requires fixture_actual",
            )

    if mode == "live" and recording.get("fixture_actual") is not None:
        report.add_error(
            "recording.fixture_actual",
            "live Recording mode must not include fixture fallback data",
        )

    check_runtime = mode == "live" and production_preflight and _recording_enabled(config)

    manager = recording.get("manager")
    if not isinstance(manager, dict):
        report.add_error("recording.manager", "live mode requires a manager object")
    else:
        _validate_runtime_value(
            manager.get("url"),
            "recording.manager.url",
            report,
            required=check_runtime,
            label="Recording Manager WebApp URL",
        )
        selection = manager.get("device_selection")
        if not isinstance(selection, dict):
            report.add_error("recording.manager.device_selection", "must be an object")
        else:
            existing_only = selection.get("use_existing_device_only")
            if not is_unresolved_placeholder(existing_only):
                if not isinstance(existing_only, bool):
                    report.add_error(
                        "recording.manager.device_selection.use_existing_device_only",
                        "must be boolean",
                    )
                elif existing_only is not True:
                    report.add_error(
                        "recording.manager.device_selection.use_existing_device_only",
                        "must be true",
                    )
            initial_state = selection.get("required_initial_state")
            if (
                not is_unresolved_placeholder(initial_state)
                and initial_state not in RECORDING_INITIAL_STATES
            ):
                report.add_error(
                    "recording.manager.device_selection.required_initial_state",
                    "must be not_recording",
                )
            status = selection.get("no_eligible_device_status")
            if not is_unresolved_placeholder(status) and status not in VALID_CHECK_STATUSES:
                report.add_error(
                    "recording.manager.device_selection.no_eligible_device_status",
                    "invalid status enum",
                )

    sites = recording.get("sites")
    if not isinstance(sites, dict):
        report.add_error("recording.sites", "must be an object")
        sites = {}
    for site_id in _site_ids(config):
        site = sites.get(site_id)
        path = f"recording.sites.{site_id}"
        if not isinstance(site, dict):
            report.add_error(path, "site observation configuration is required")
            continue

        webapp = site.get("webapp")
        if not isinstance(webapp, dict):
            report.add_error(f"{path}.webapp", "must be an object")
        else:
            _validate_runtime_value(
                webapp.get("url"),
                f"{path}.webapp.url",
                report,
                required=check_runtime,
                label=f"Recording {site_id} WebApp URL",
            )
            capture = webapp.get("capture_baseline_at_test_start")
            if not is_unresolved_placeholder(capture) and capture is not True:
                report.add_error(
                    f"{path}.webapp.capture_baseline_at_test_start",
                    "must be true",
                )

        server = site.get("server")
        if not isinstance(server, dict):
            report.add_error(f"{path}.server", "must be an object")
        else:
            reference = server.get("connection_reference")
            if not is_unresolved_placeholder(reference) and (
                not isinstance(reference, str) or not reference.strip()
            ):
                report.add_error(
                    f"{path}.server.connection_reference",
                    "must be a non-empty connection reference",
                )
            capture = server.get("capture_baseline_at_test_start")
            if not is_unresolved_placeholder(capture) and capture is not True:
                report.add_error(
                    f"{path}.server.capture_baseline_at_test_start",
                    "must be true",
                )

    validation = recording.get("validation")
    if not isinstance(validation, dict):
        report.add_error("recording.validation", "must be an object")
    else:
        baseline = validation.get("baseline")
        if not isinstance(baseline, dict):
            report.add_error("recording.validation.baseline", "must be an object")
        elif baseline.get("capture_at_test_start") is not True and not is_unresolved_placeholder(
            baseline.get("capture_at_test_start")
        ):
            report.add_error("recording.validation.baseline.capture_at_test_start", "must be true")

        after_start = validation.get("after_start")
        if not isinstance(after_start, dict):
            report.add_error("recording.validation.after_start", "must be an object")
        else:
            for key in [
                "site1_webapp_count_delta",
                "site2_webapp_count_delta",
                "site1_server_count_delta",
                "site2_server_count_delta",
            ]:
                value = after_start.get(key)
                if is_unresolved_placeholder(value):
                    continue
                if not isinstance(value, int) or isinstance(value, bool) or value != 1:
                    report.add_error(f"recording.validation.after_start.{key}", "must be integer 1")

        after_stop = validation.get("after_stop")
        if not isinstance(after_stop, dict):
            report.add_error("recording.validation.after_stop", "must be an object")
        else:
            for key in [
                "site1_webapp_must_return_to_baseline",
                "site2_webapp_must_return_to_baseline",
                "site1_server_must_return_to_baseline",
                "site2_server_must_return_to_baseline",
            ]:
                value = after_stop.get(key)
                if not is_unresolved_placeholder(value) and value is not True:
                    report.add_error(f"recording.validation.after_stop.{key}", "must be true")

    polling = recording.get("polling")
    if not isinstance(polling, dict):
        report.add_error("recording.polling", "must be an object")
    else:
        _validate_positive_number(
            polling.get("timeout_seconds"),
            "recording.polling.timeout_seconds",
            report,
        )
        _validate_positive_number(
            polling.get("interval_seconds"),
            "recording.polling.interval_seconds",
            report,
        )

    result_policy = recording.get("result_policy")
    if not isinstance(result_policy, dict):
        report.add_error(
            "recording.result_policy",
            "must be an object",
        )
    else:
        expected_result_policy = {
            "functional_mismatch_status": "FAIL",
            "technical_failure_status": "ERROR",
            "cleanup_failure_status": "ERROR",
        }
        for key, expected_status in expected_result_policy.items():
            value = result_policy.get(key)
            if is_unresolved_placeholder(value):
                continue
            if value != expected_status:
                report.add_error(
                    f"recording.result_policy.{key}",
                    f"must be {expected_status}",
                )
        requires_recovery = result_policy.get("cleanup_failure_requires_recovery")
        if not is_unresolved_placeholder(requires_recovery) and requires_recovery is not True:
            report.add_error(
                "recording.result_policy.cleanup_failure_requires_recovery",
                "must be true",
            )


def _validate_servers(
    config: dict[str, Any],
    report: ValidationReport,
    *,
    production_preflight: bool,
) -> None:
    servers_config = config.get("servers", {})
    if not isinstance(servers_config, dict):
        report.add_error("servers", "must be an object")
        return

    ssh = servers_config.get("ssh")
    if not isinstance(ssh, dict):
        report.add_error("servers.ssh", "must be an object")
    else:
        username = ssh.get("username")
        if not is_unresolved_placeholder(username) and (
            not isinstance(username, str) or not username.strip()
        ):
            report.add_error("servers.ssh.username", "must be a non-empty string")

        auth = ssh.get("auth")
        if not is_unresolved_placeholder(auth) and auth not in SSH_SUPPORTED_AUTH_TYPES:
            report.add_error("servers.ssh.auth", "must be private_key")

        host_policy = ssh.get("host_key_policy")
        if (
            not is_unresolved_placeholder(host_policy)
            and host_policy not in SSH_SUPPORTED_HOST_KEY_POLICIES
        ):
            report.add_error("servers.ssh.host_key_policy", "must be strict")

        _validate_positive_number(ssh.get("connect_timeout"), "servers.ssh.connect_timeout", report)
        _validate_positive_number(ssh.get("command_timeout"), "servers.ssh.command_timeout", report)

        private_key_path = ssh.get("private_key_path")
        if (
            auth == "private_key"
            and production_preflight
            and _infrastructure_enabled(config)
            and _runtime_unset(private_key_path)
        ):
            report.add_error(
                "servers.ssh.private_key_path",
                "SSH_PRIVATE_KEY_PATH runtime value is missing or unresolved",
            )
        elif not _runtime_unset(private_key_path) and (
            not isinstance(private_key_path, str) or not private_key_path.strip()
        ):
            report.add_error("servers.ssh.private_key_path", "must be a non-empty string")

        known_hosts_path = ssh.get("known_hosts_path")
        if (
            host_policy == "strict"
            and production_preflight
            and _infrastructure_enabled(config)
            and _runtime_unset(known_hosts_path)
        ):
            report.add_error(
                "servers.ssh.known_hosts_path",
                "SSH_KNOWN_HOSTS_PATH runtime value is missing or unresolved",
            )
        elif not _runtime_unset(known_hosts_path) and (
            not isinstance(known_hosts_path, str) or not known_hosts_path.strip()
        ):
            report.add_error("servers.ssh.known_hosts_path", "must be a non-empty string")

    sites = servers_config.get("sites")
    if not isinstance(sites, dict) or not sites:
        report.add_error("servers.sites", "must define site server inventories")
        return

    for site_id in _site_ids(config):
        site = sites.get(site_id)
        site_path = f"servers.sites.{site_id}"
        if not isinstance(site, dict):
            report.add_error(site_path, "site server inventory is required")
            continue
        server_list = site.get("servers")
        if not isinstance(server_list, list) or not server_list:
            report.add_error(f"{site_path}.servers", "must be a non-empty list")
            continue

        seen_ids: set[str] = set()
        for idx, server in enumerate(server_list):
            path = f"{site_path}.servers[{idx}]"
            if not isinstance(server, dict):
                report.add_error(path, "server must be an object")
                continue

            server_id = server.get("id")
            if not is_unresolved_placeholder(server_id):
                if not isinstance(server_id, str) or not server_id.strip():
                    report.add_error(f"{path}.id", "must be a non-empty string")
                elif server_id in seen_ids:
                    report.add_error(f"{path}.id", f"duplicate server id {server_id!r}")
                else:
                    seen_ids.add(server_id)

            hostname = server.get("hostname")
            if not is_unresolved_placeholder(hostname) and (
                not isinstance(hostname, str) or not hostname.strip()
            ):
                report.add_error(f"{path}.hostname", "must be a non-empty string")

            required = server.get("required")
            if not is_unresolved_placeholder(required) and not isinstance(required, bool):
                report.add_error(f"{path}.required", "must be boolean")

            port = server.get("ssh_port")
            if not is_unresolved_placeholder(port):
                if not isinstance(port, int) or isinstance(port, bool) or not (1 <= port <= 65535):
                    report.add_error(f"{path}.ssh_port", "must be an integer from 1 to 65535")

            if "nfs_mounts" in server:
                report.add_error(
                    f"{path}.nfs_mounts",
                    "NFS validation was removed; delete this section",
                )

            filesystems = server.get("filesystems")
            if not isinstance(filesystems, list) or not filesystems:
                report.add_error(
                    f"{path}.filesystems",
                    "must be a non-empty list",
                )
            else:
                if len(filesystems) != 1:
                    report.add_error(
                        f"{path}.filesystems",
                        "must contain exactly one root filesystem check",
                    )
                for fs_idx, filesystem in enumerate(filesystems):
                    fs_path = f"{path}.filesystems[{fs_idx}]"
                    if not isinstance(filesystem, dict):
                        report.add_error(
                            fs_path,
                            "filesystem check must be an object",
                        )
                        continue
                    fs_expected = {
                        "path": "/",
                        "command": "df -h /",
                    }
                    for field_name, expected_value in fs_expected.items():
                        value = filesystem.get(field_name)
                        if is_unresolved_placeholder(value):
                            continue
                        if value != expected_value:
                            report.add_error(
                                f"{fs_path}.{field_name}",
                                f"must be {expected_value!r}",
                            )
                    required_fs = filesystem.get("required")
                    if (
                        not is_unresolved_placeholder(required_fs)
                        and not isinstance(required_fs, bool)
                    ):
                        report.add_error(
                            f"{fs_path}.required",
                            "must be boolean",
                        )
                    _validate_percentage(
                        filesystem.get("warning_percent"),
                        f"{fs_path}.warning_percent",
                        report,
                    )
                    _validate_percentage(
                        filesystem.get("critical_percent"),
                        f"{fs_path}.critical_percent",
                        report,
                    )

            chrony = server.get("chrony")
            if not isinstance(chrony, list) or not chrony:
                report.add_error(f"{path}.chrony", "must be a non-empty list")
            else:
                for chrony_idx, check in enumerate(chrony):
                    check_path = f"{path}.chrony[{chrony_idx}]"
                    if not isinstance(check, dict):
                        report.add_error(check_path, "Chrony check must be an object")
                        continue
                    for field_name in ["timezone", "source"]:
                        value = check.get(field_name)
                        if is_unresolved_placeholder(value):
                            continue
                        if not isinstance(value, str) or not value.strip():
                            report.add_error(
                                f"{check_path}.{field_name}", "must be a non-empty string"
                            )
                    required_chrony = check.get("required")
                    if not is_unresolved_placeholder(required_chrony) and not isinstance(
                        required_chrony, bool
                    ):
                        report.add_error(f"{check_path}.required", "must be boolean")
                    _validate_number(
                        check.get("warning_offset"), f"{check_path}.warning_offset", report
                    )
                    _validate_number(
                        check.get("critical_offset"), f"{check_path}.critical_offset", report
                    )


def _validate_runtime_value(
    value: Any,
    path: str,
    report: ValidationReport,
    *,
    required: bool,
    label: str,
) -> None:
    if is_unresolved_placeholder(value):
        if required:
            report.add_error(path, f"{label} runtime value is missing or unresolved")
        return
    if not isinstance(value, str) or not value.strip():
        if required:
            report.add_error(path, f"{label} runtime value is required")


def _validate_number(value: Any, path: str, report: ValidationReport) -> None:
    if is_unresolved_placeholder(value):
        return
    if not isinstance(value, int | float) or isinstance(value, bool) or value < 0:
        report.add_error(path, "must be a non-negative number")


def _validate_positive_number(value: Any, path: str, report: ValidationReport) -> None:
    if is_unresolved_placeholder(value):
        return
    if not isinstance(value, int | float) or isinstance(value, bool) or value <= 0:
        report.add_error(path, "must be a positive number")


def _validate_integer(value: Any, path: str, report: ValidationReport) -> None:
    if is_unresolved_placeholder(value):
        return
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        report.add_error(path, "must be a non-negative integer")


def _validate_positive_integer(value: Any, path: str, report: ValidationReport) -> None:
    if is_unresolved_placeholder(value):
        return
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        report.add_error(path, "must be a positive integer")


def _validate_percentage(value: Any, path: str, report: ValidationReport) -> None:
    if is_unresolved_placeholder(value):
        return
    if not isinstance(value, int | float) or isinstance(value, bool) or not (0 <= value <= 100):
        report.add_error(path, "must be a number from 0 through 100")


def _portainer_enabled(config: dict[str, Any]) -> bool:
    module = _module_rules(config).get("portainer", {})
    return bool(module.get("enabled") and module.get("required"))


def _rabbitmq_enabled(config: dict[str, Any]) -> bool:
    module = _module_rules(config).get("rabbitmq", {})
    return bool(module.get("enabled") and module.get("required"))


def _recording_enabled(config: dict[str, Any]) -> bool:
    module = _module_rules(config).get("recording", {})
    return bool(module.get("enabled") and module.get("required"))


def _infrastructure_enabled(config: dict[str, Any]) -> bool:
    module = _module_rules(config).get("infrastructure", {})
    return bool(module.get("enabled") and module.get("required"))

def _validate_manual_db_check(
    config: dict[str, Any],
    report: ValidationReport,
) -> None:
    manual_db_check = config.get("manual_db_check")

    if not isinstance(manual_db_check, dict):
        report.add_error(
            "manual_db_check",
            "must be an object",
        )
        return

    enabled = manual_db_check.get("enabled")
    required = manual_db_check.get("required")

    if not isinstance(enabled, bool):
        report.add_error(
            "manual_db_check.enabled",
            "must be boolean",
        )

    if not isinstance(required, bool):
        report.add_error(
            "manual_db_check.required",
            "must be boolean",
        )

    if enabled is False and required is True:
        report.add_error(
            "manual_db_check.required",
            "cannot be true when the manual DB check is disabled",
        )

    if enabled is not True:
        return

    display_name = manual_db_check.get("display_name")

    if (
        not isinstance(display_name, str)
        or not display_name.strip()
        or is_unresolved_placeholder(display_name)
    ):
        report.add_error(
            "manual_db_check.display_name",
            "must be a resolved non-empty string when enabled",
        )

    script_path = manual_db_check.get("script_path")

    if (
        not isinstance(script_path, str)
        or not script_path.strip()
        or is_unresolved_placeholder(script_path)
    ):
        report.add_error(
            "manual_db_check.script_path",
            "must be a resolved non-empty Windows PowerShell script path when enabled",
        )
        return

    if any(
        char in script_path
        for char in ('"', "\r", "\n")
    ):
        report.add_error(
            "manual_db_check.script_path",
            "contains unsupported characters",
        )
        return

    windows_path = PureWindowsPath(
        script_path.strip()
    )

    if not windows_path.is_absolute():
        report.add_error(
            "manual_db_check.script_path",
            "must be an absolute Windows path",
        )

    if windows_path.suffix.lower() != ".ps1":
        report.add_error(
            "manual_db_check.script_path",
            "must reference a .ps1 file",
        )

def _validate_thresholds(config: dict[str, Any], report: ValidationReport) -> None:
    threshold_pairs = {
        "critical_percent": "warning_percent",
        "critical_offset": "warning_offset",
    }
    for path, value in iter_leaves(config):
        for critical_name, warning_name in threshold_pairs.items():
            if not path.endswith(critical_name):
                continue
            warning_path = path.removesuffix(critical_name) + warning_name
            warning = _get_path(config, warning_path)
            if (
                isinstance(value, int | float)
                and not isinstance(value, bool)
                and isinstance(warning, int | float)
                and not isinstance(warning, bool)
                and value <= warning
            ):
                report.add_error(
                    path,
                    "critical threshold must be greater than warning threshold",
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
    for message in runtime_identity_errors(
        config,
        production_preflight=production_preflight,
    ):
        report.add_error(
            "runtime.traceability",
            message,
        )

    if not production_preflight:
        return

    if _runtime_unset(os.getenv("WEEKEND_REPORT_CSRF_SIGNING_KEY")):
        report.add_error(
            "runtime.csrf",
            "WEEKEND_REPORT_CSRF_SIGNING_KEY must be set for production browser mutations",
        )

    csrf_ttl_raw = os.getenv(
        "WEEKEND_REPORT_CSRF_TTL_SECONDS",
        "3600",
    ).strip()

    try:
        csrf_ttl = int(csrf_ttl_raw)
        if csrf_ttl <= 0:
            raise ValueError
    except ValueError:
        report.add_error(
            "runtime.csrf",
            "WEEKEND_REPORT_CSRF_TTL_SECONDS must be a positive integer",
        )

def _runtime_unset(value: str | None) -> bool:
    return (
        value is None or value.strip() in UNSET_RUNTIME_VALUES or is_unresolved_placeholder(value)
    )


def _validate_placeholders(
    config: dict[str, Any],
    report: ValidationReport,
    *,
    production_preflight: bool,
) -> None:
    unresolved_is_blocking = production_preflight and _has_enabled_required_automation(config)
    for path, value in iter_leaves(config):
        if value == PLACEHOLDER_NOT_APPLICABLE:
            if not any(part in path for part in NOT_APPLICABLE_ALLOWED_PATH_PARTS):
                report.add_error(path, "<NOT_APPLICABLE> is not permitted for this field")
            continue
        if not is_unresolved_placeholder(value):
            continue
        message = f"unresolved value {value}"
        if unresolved_is_blocking:
            report.add_error(path, message)
        else:
            report.add_warning(path, message)
