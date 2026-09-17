from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from collections.abc import Mapping, MutableMapping
from pathlib import Path
from typing import Any

from app.config.schema import REQUIRED_FILES, is_unresolved_placeholder


class ConfigError(ValueError):
    pass


ENV_FILE_ORDER = [
    "app.env",
    "portainer.env",
    "doctor.env",
    "rabbitmq.env",
    "recording.env",
    "infrastructure.env",
    "splunk.env",
]

UNSET_RUNTIME_VALUES = {"", "<TBD>", "<TO_VERIFY>", "UNKNOWN"}
SENSITIVE_KEY_PARTS = {
    "api-key",
    "authorization",
    "cookie",
    "private-key",
    "password",
    "secret",
    "signing-key",
    "token",
    "x-api-key",
}
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _load_text(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml

        loaded = yaml.safe_load(text)
    except ModuleNotFoundError:
        loaded = json.loads(text)
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ConfigError(f"{path.name}: expected mapping at top level")
    return loaded


def load_config_dir(
    config_dir: str | Path,
    *,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    root = Path(config_dir)
    rules_path = root / "rules.yml"
    if not rules_path.exists():
        raise ConfigError("Missing required configuration file: rules.yml")

    env_map = {str(key): str(value) for key, value in (env or os.environ).items()}
    rules = _load_text(rules_path)
    data: dict[str, Any] = {
        "_config_dir": str(root),
        "_project_root": str(Path(__file__).resolve().parents[2]),
        "rules": rules,
    }
    data.update(_deployment_config_from_env(env_map, rules))
    data["_config_hash"] = config_hash(root, effective_config=data)
    return data


def parse_env_file(path: str | Path) -> dict[str, str]:
    env_path = Path(path)
    values: dict[str, str] = {}
    for line_number, line in enumerate(env_path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[7:].lstrip()
        if "=" not in stripped:
            raise ConfigError(f"{env_path.name}:{line_number}: expected NAME=value")
        name, value = stripped.split("=", 1)
        name = name.strip()
        if not ENV_NAME_RE.match(name):
            raise ConfigError(f"{env_path.name}:{line_number}: invalid environment variable name")
        values[name] = _unquote_env_value(value.strip())
    return values


def load_env_files(
    paths: list[str | Path],
    *,
    environ: MutableMapping[str, str] | None = None,
    override: bool = True,
) -> None:
    target = os.environ if environ is None else environ
    for path in paths:
        for name, value in parse_env_file(path).items():
            if override or name not in target:
                target[name] = value


def config_hash(
    config_dir: str | Path,
    *,
    effective_config: dict[str, Any] | None = None,
) -> str:
    root = Path(config_dir)
    h = hashlib.sha256()
    for filename in REQUIRED_FILES:
        path = root / filename
        if path.exists():
            h.update(filename.encode("utf-8"))
            h.update(path.read_bytes())
    if effective_config is not None:
        payload = _sanitize_for_hash(effective_config)
        h.update(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return h.hexdigest()


def _deployment_config_from_env(env: Mapping[str, str], rules: dict[str, Any]) -> dict[str, Any]:
    site_entries = _load_sites(env)
    site_refs = [
        (idx, str(site.get("id") or f"site{idx}"))
        for idx, site in site_entries
        if isinstance(site, dict)
    ]
    sites = [site for _, site in site_entries]
    return {
        "sites": {"sites": sites},
        "portainer_expected": _load_portainer(env, rules, site_refs),
        "doctor": {"doctor": _load_doctor(env, rules)},
        "rabbitmq_expected": _load_rabbitmq(env, rules, site_refs),
        "recording": _load_recording(env, rules, site_refs),
        "servers": _load_servers(env, rules, site_refs),
        "splunk_dashboards": _load_splunk(env, rules),
        "manual_db_check": _load_manual_db_check(env),
    }


def _load_sites(env: Mapping[str, str]) -> list[tuple[int, dict[str, Any]]]:
    count = _env_int(env, "SITE_COUNT", default=0, min_value=0)
    sites: list[tuple[int, dict[str, Any]]] = []
    for idx in range(1, count + 1):
        sites.append(
            (
                idx,
                {
                    "id": _env_string(env, f"SITE{idx}_ID"),
                    "display_name": _env_string(env, f"SITE{idx}_DISPLAY_NAME"),
                    "purpose": _env_string(env, f"SITE{idx}_PURPOSE", "weekend_report_site"),
                },
            )
        )
    return sites


def _load_portainer(
    env: Mapping[str, str],
    rules: dict[str, Any],
    site_refs: list[tuple[int, str]],
) -> dict[str, Any]:
    policy = _mapping_at(rules, "portainer")
    connection_policy = _mapping_at(policy, "connection")
    sites: dict[str, Any] = {}
    for idx, site_id in site_refs:
        prefix = f"PORTAINER_SITE{idx}"
        sites[site_id] = {
            "environment_type": _env_string(
                env,
                f"{prefix}_ENVIRONMENT_TYPE",
                str(policy.get("environment_type", "docker_swarm")),
            ),
            "connection": {
                "url": _env_string(env, f"{prefix}_URL"),
                "endpoint_id": _env_string(env, f"{prefix}_ENDPOINT_ID"),
                "api_contract": _env_string(
                    env,
                    f"{prefix}_API_CONTRACT",
                    _env_string(env, "PORTAINER_API_CONTRACT", "docker_proxy_v1"),
                ),
                "auth": {
                    "type": _env_string(
                        env,
                        f"{prefix}_AUTH_TYPE",
                        _env_string(env, "PORTAINER_AUTH_TYPE", "x_api_key"),
                    ),
                    "token": _env_string(env, f"{prefix}_TOKEN"),
                },
                "tls": {
                    "verify": _env_tls_verify(env, f"{prefix}_TLS_VERIFY", default=True),
                    "ca_file": _env_string(env, f"{prefix}_CA_FILE"),
                },
                "timeouts": copy.deepcopy(
                    connection_policy.get(
                        "timeouts",
                        {"connect_seconds": 5, "read_seconds": 20},
                    )
                ),
                "retries": copy.deepcopy(
                    connection_policy.get(
                        "retries",
                        {"attempts": 1, "backoff_seconds": 0},
                    )
                ),
            },
        }
    return {
        "collection_mode": _env_string(env, "PORTAINER_COLLECTION_MODE", "live"),
        "sites": sites,
    }


def _load_doctor(env: Mapping[str, str], rules: dict[str, Any]) -> dict[str, Any]:
    policy = _mapping_at(rules, "doctor")
    return {
        "mode": _env_string(env, "DOCTOR_MODE", "api"),
        "api": {
            "site1_url": _env_string(env, "DOCTOR_SITE1_API_URL"),
            "site2_url": _env_string(env, "DOCTOR_SITE2_API_URL"),
        },
        "validation": copy.deepcopy(policy.get("validation", {})),
        "manual_review": copy.deepcopy(policy.get("manual_review", {})),
    }


def _load_rabbitmq(
    env: Mapping[str, str],
    rules: dict[str, Any],
    site_refs: list[tuple[int, str]],
) -> dict[str, Any]:
    policy = _mapping_at(rules, "rabbitmq")
    connection_policy = _mapping_at(policy, "connection")
    sites: dict[str, Any] = {}
    connections: dict[str, Any] = {}
    for idx, site_id in site_refs:
        prefix = f"RABBITMQ_SITE{idx}"
        sites[site_id] = {"required": _env_bool(env, f"{prefix}_REQUIRED", default=True)}
        connections[site_id] = {
            "url": _env_string(env, f"{prefix}_URL"),
            "user": _env_string(env, f"{prefix}_USER"),
            "password": _env_string(env, f"{prefix}_PASSWORD"),
            "tls_verify": _env_tls_verify(
                env,
                f"{prefix}_TLS_VERIFY",
                default=True,
            ),
            "ca_file": _env_string(
                env,
                f"{prefix}_CA_FILE",
            ),
            "timeout_seconds": _env_float(
                env,
                f"{prefix}_TIMEOUT_SECONDS",
                default=float(connection_policy.get("timeout_seconds", 5)),
                min_value=0.001,
            ),
            "retry_attempts": _env_int(
                env,
                f"{prefix}_RETRY_ATTEMPTS",
                default=int(connection_policy.get("retry_attempts", 1)),
                min_value=1,
            ),
        }
    return {
        "collection_mode": _env_string(env, "RABBITMQ_COLLECTION_MODE", "live"),
        "connections": connections,
        "queues": copy.deepcopy(policy.get("queues", {})),
        "nodes": copy.deepcopy(policy.get("nodes", {})),
        "sites": sites,
    }


def _load_recording(
    env: Mapping[str, str],
    rules: dict[str, Any],
    site_refs: list[tuple[int, str]],
) -> dict[str, Any]:
    policy = _mapping_at(rules, "recording")
    sites: dict[str, Any] = {}
    for idx, site_id in site_refs:
        prefix = f"RECORDING_SITE{idx}"
        sites[site_id] = {
            "webapp": {
                "url": _env_string(env, f"{prefix}_WEBAPP_URL"),
                "capture_baseline_at_test_start": True,
            },
            "server": {
                "connection_reference": _env_string(env, f"{prefix}_SERVER_REFERENCE"),
                "capture_baseline_at_test_start": True,
            },
        }
    return {
        "collection_mode": _env_string(env, "RECORDING_COLLECTION_MODE", "live"),
        "workflow": policy.get("workflow", "existing_device_start_stop"),
        "safety": copy.deepcopy(policy.get("safety", {})),
        "manager": {
            "url": _env_string(env, "RECORDING_MANAGER_WEBAPP_URL"),
            "device_selection": copy.deepcopy(
                _mapping_at(policy, "manager").get("device_selection", {})
            ),
        },
        "sites": sites,
        "validation": copy.deepcopy(policy.get("validation", {})),
        "polling": copy.deepcopy(policy.get("polling", {})),
        "result_policy": copy.deepcopy(policy.get("result_policy", {})),
    }


def _load_servers(
    env: Mapping[str, str],
    rules: dict[str, Any],
    site_refs: list[tuple[int, str]],
) -> dict[str, Any]:
    policy = _mapping_at(rules, "infrastructure")
    ssh_policy = _mapping_at(policy, "ssh")
    filesystem_policy = copy.deepcopy(policy.get("filesystem", {}))
    chrony_policy = copy.deepcopy(policy.get("chrony", {}))
    sites: dict[str, Any] = {}
    for idx, site_id in site_refs:
        server_count = _env_int(env, f"SITE{idx}_SERVER_COUNT", default=0, min_value=0)
        servers = []
        for server_idx in range(1, server_count + 1):
            prefix = f"SITE{idx}_SERVER_{server_idx}"
            chrony = copy.deepcopy(chrony_policy)
            chrony["timezone"] = _env_string(
                env,
                f"{prefix}_CHRONY_TIMEZONE",
                _env_string(env, "CHRONY_TIMEZONE"),
            )
            chrony["source"] = _env_string(
                env,
                f"{prefix}_CHRONY_SOURCE",
                _env_string(env, "CHRONY_SOURCE"),
            )
            servers.append(
                {
                    "id": _env_string(env, f"{prefix}_ID"),
                    "hostname": _env_string(env, f"{prefix}_HOST"),
                    "required": _env_bool(env, f"{prefix}_REQUIRED", default=True),
                    "ssh_port": _env_int(env, f"{prefix}_PORT", default=22, min_value=1),
                    "filesystems": [copy.deepcopy(filesystem_policy)],
                    "chrony": [chrony],
                }
            )
        sites[site_id] = {"servers": servers}
    return {
        "ssh": {
            "username": _env_string(env, "SSH_USERNAME"),
            "auth": ssh_policy.get("auth", "private_key"),
            "host_key_policy": ssh_policy.get("host_key_policy", "strict"),
            "private_key_path": _env_string(env, "SSH_PRIVATE_KEY_PATH"),
            "known_hosts_path": _env_string(env, "SSH_KNOWN_HOSTS_PATH"),
            "connect_timeout": ssh_policy.get("connect_timeout", 5),
            "command_timeout": ssh_policy.get("command_timeout", 5),
        },
        "sites": sites,
    }


def _load_splunk(env: Mapping[str, str], rules: dict[str, Any]) -> dict[str, Any]:
    policy = _mapping_at(rules, "splunk")
    defaults = _mapping_at(policy, "dashboard_defaults")
    count = _env_int(env, "SPLUNK_DASHBOARD_COUNT", default=0, min_value=0)
    dashboards = []
    for idx in range(1, count + 1):
        prefix = f"SPLUNK_DASHBOARD_{idx}"
        dashboards.append(
            {
                "id": _env_string(env, f"{prefix}_ID"),
                "display_name": _env_string(
                    env,
                    f"{prefix}_DISPLAY_NAME",
                    _env_string(env, f"{prefix}_NAME"),
                ),
                "url": _env_string(env, f"{prefix}_URL"),
                "required_review": _env_bool(
                    env,
                    f"{prefix}_REQUIRED_REVIEW",
                    default=bool(defaults.get("required_review", True)),
                ),
                "note_required": _env_bool(
                    env,
                    f"{prefix}_NOTE_REQUIRED",
                    default=bool(defaults.get("note_required", False)),
                ),
                "order": _env_int(
                    env,
                    f"{prefix}_ORDER",
                    default=idx,
                    min_value=1,
                ),
            }
        )
    return {
        "dashboards": dashboards,
        "open_all": copy.deepcopy(policy.get("open_all", {"include_optional": True})),
    }

def _load_manual_db_check(env: Mapping[str, str]) -> dict[str, Any]:
    return {
        "enabled": _env_bool(
            env,
            "WEEKEND_REPORT_DB_MANUAL_CHECK_ENABLED",
            default=False,
        ),
        "display_name": _env_string(
            env,
            "WEEKEND_REPORT_DB_MANUAL_CHECK_DISPLAY_NAME",
            "Database Synchronization Check",
        ),
        "script_path": _env_string(
            env,
            "WEEKEND_REPORT_DB_MANUAL_CHECK_SCRIPT_PATH",
        ),
        "required": _env_bool(
            env,
            "WEEKEND_REPORT_DB_MANUAL_CHECK_REQUIRED",
            default=False,
        ),
    }

def _mapping_at(config: dict[str, Any], *parts: str) -> dict[str, Any]:
    current: Any = config
    for part in parts:
        if not isinstance(current, dict):
            return {}
        current = current.get(part, {})
    return current if isinstance(current, dict) else {}


def _env_string(env: Mapping[str, str], name: str, default: str = "") -> str:
    value = env.get(name)
    if value is None:
        return default
    return str(value).strip()


def _env_bool(env: Mapping[str, str], name: str, *, default: bool) -> bool:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise ConfigError(f"{name} must be true or false")


def _env_tls_verify(env: Mapping[str, str], name: str, *, default: bool) -> bool | str:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    normalized = raw.strip().lower()
    if normalized == "custom_ca":
        return "custom_ca"
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise ConfigError(f"{name} must be true, false, or custom_ca")


def _env_int(
    env: Mapping[str, str],
    name: str,
    *,
    default: int,
    min_value: int | None = None,
) -> int:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc
    if min_value is not None and value < min_value:
        raise ConfigError(f"{name} must be at least {min_value}")
    return value


def _env_float(
    env: Mapping[str, str],
    name: str,
    *,
    default: float,
    min_value: float | None = None,
) -> float:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw.strip())
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number") from exc
    if min_value is not None and value < min_value:
        raise ConfigError(f"{name} must be at least {min_value}")
    return value


def _unquote_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _sanitize_for_hash(value: Any, key: str = "") -> Any:
    if key.startswith("_"):
        return None
    if key == "fixture_actual":
        return "<FIXTURE_OMITTED>"
    if _sensitive_key(key):
        return "<REDACTED>" if not _runtime_unset(value) else ""
    if isinstance(value, dict):
        return {
            str(item_key): _sanitize_for_hash(item_value, str(item_key))
            for item_key, item_value in value.items()
            if not str(item_key).startswith("_")
        }
    if isinstance(value, list):
        return [_sanitize_for_hash(item) for item in value]
    return value


def _sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("_", "-")
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def _runtime_unset(value: Any) -> bool:
    return (
        value is None
        or (isinstance(value, str) and value.strip() in UNSET_RUNTIME_VALUES)
        or is_unresolved_placeholder(value)
    )
