from __future__ import annotations

import copy
from pathlib import Path

from app.config.loader import load_config_dir

DOCTOR_SERVICES = [f"service-{idx:02d}" for idx in range(1, 18)]


def valid_env() -> dict[str, str]:
    return {
        "SITE_COUNT": "2",
        "SITE1_ID": "site1",
        "SITE1_DISPLAY_NAME": "Fixture Site 1",
        "SITE1_PURPOSE": "local fixture",
        "SITE2_ID": "site2",
        "SITE2_DISPLAY_NAME": "Fixture Site 2",
        "SITE2_PURPOSE": "local fixture",
        "WEEKEND_REPORT_APP_VERSION": "fixture-version",
        "WEEKEND_REPORT_BUILD_ID": "fixture-build",
        "WEEKEND_REPORT_CSRF_SIGNING_KEY": "fixture-csrf-signing-key",
        "WEEKEND_REPORT_CSRF_TTL_SECONDS": "3600",
        "WEEKEND_REPORT_DB_MANUAL_CHECK_ENABLED": "true",
        "WEEKEND_REPORT_DB_MANUAL_CHECK_DISPLAY_NAME": "Database Synchronization Check",
        "WEEKEND_REPORT_DB_MANUAL_CHECK_SCRIPT_PATH": (
            "C:\\Scripts\\DatabaseSync\\database_sync_check.ps1"
        ),
        "WEEKEND_REPORT_DB_MANUAL_CHECK_REQUIRED": "true",
        "PORTAINER_COLLECTION_MODE": "live",
        "PORTAINER_API_CONTRACT": "docker_proxy_v1",
        "PORTAINER_AUTH_TYPE": "x_api_key",
        "PORTAINER_SITE1_URL": "https://portainer-site1.example.invalid",
        "PORTAINER_SITE1_ENDPOINT_ID": "1",
        "PORTAINER_SITE1_TOKEN": "fixture-portainer-token-1",
        "PORTAINER_SITE1_TLS_VERIFY": "true",
        "PORTAINER_SITE2_URL": "https://portainer-site2.example.invalid",
        "PORTAINER_SITE2_ENDPOINT_ID": "2",
        "PORTAINER_SITE2_TOKEN": "fixture-portainer-token-2",
        "PORTAINER_SITE2_TLS_VERIFY": "true",
        "DOCTOR_MODE": "api",
        "DOCTOR_SITE1_API_URL": "https://doctor-site1.example.invalid/doctor-api",
        "DOCTOR_SITE2_API_URL": "https://doctor-site2.example.invalid/doctor-api",
        "RABBITMQ_COLLECTION_MODE": "live",
        "RABBITMQ_SITE1_URL": "https://rabbitmq-site1.example.invalid",
        "RABBITMQ_SITE1_USER": "fixture-user-1",
        "RABBITMQ_SITE1_PASSWORD": "fixture-password-1",
        "RABBITMQ_SITE1_TLS_VERIFY": "true",
        "RABBITMQ_SITE1_CA_FILE": "",
        "RABBITMQ_SITE2_URL": "https://rabbitmq-site2.example.invalid",
        "RABBITMQ_SITE2_USER": "fixture-user-2",
        "RABBITMQ_SITE2_PASSWORD": "fixture-password-2",
        "RABBITMQ_SITE2_TLS_VERIFY": "true",
        "RABBITMQ_SITE2_CA_FILE": "",
        "RECORDING_COLLECTION_MODE": "live",
        "RECORDING_MANAGER_WEBAPP_URL": "https://recording-manager.example.invalid",
        "RECORDING_SITE1_WEBAPP_URL": "https://recording-site1.example.invalid",
        "RECORDING_SITE1_SERVER_REFERENCE": "srv1",
        "RECORDING_SITE2_WEBAPP_URL": "https://recording-site2.example.invalid",
        "RECORDING_SITE2_SERVER_REFERENCE": "srv2",
        "SSH_USERNAME": "fixture",
        "SSH_PRIVATE_KEY_PATH": "/run/secrets/fixture-key",
        "SSH_KNOWN_HOSTS_PATH": "/run/secrets/fixture-known-hosts",
        "CHRONY_TIMEZONE": "UTC",
        "CHRONY_SOURCE": "fixture-ntp",
        "SITE1_SERVER_COUNT": "1",
        "SITE1_SERVER_1_ID": "srv1",
        "SITE1_SERVER_1_HOST": "fixture1",
        "SITE1_SERVER_1_PORT": "22",
        "SITE1_SERVER_1_REQUIRED": "true",
        "SITE2_SERVER_COUNT": "1",
        "SITE2_SERVER_1_ID": "srv2",
        "SITE2_SERVER_1_HOST": "fixture2",
        "SITE2_SERVER_1_PORT": "22",
        "SITE2_SERVER_1_REQUIRED": "true",
        "SPLUNK_DASHBOARD_COUNT": "4",
        "SPLUNK_DASHBOARD_1_ID": "system_health",
        "SPLUNK_DASHBOARD_1_DISPLAY_NAME": "System Health",
        "SPLUNK_DASHBOARD_1_URL": "https://example.invalid/splunk/system-health",
        "SPLUNK_DASHBOARD_2_ID": "recording",
        "SPLUNK_DASHBOARD_2_DISPLAY_NAME": "Recording",
        "SPLUNK_DASHBOARD_2_URL": "https://example.invalid/splunk/recording",
        "SPLUNK_DASHBOARD_3_ID": "infrastructure",
        "SPLUNK_DASHBOARD_3_DISPLAY_NAME": "Infrastructure",
        "SPLUNK_DASHBOARD_3_URL": "https://example.invalid/splunk/infrastructure",
        "SPLUNK_DASHBOARD_4_ID": "errors",
        "SPLUNK_DASHBOARD_4_DISPLAY_NAME": "Errors",
        "SPLUNK_DASHBOARD_4_URL": "https://example.invalid/splunk/errors",
    }


def fixture_env() -> dict[str, str]:
    env = valid_env()
    env.update(
        {
            "PORTAINER_COLLECTION_MODE": "fixture",
            "RABBITMQ_COLLECTION_MODE": "fixture",
            "RECORDING_COLLECTION_MODE": "fixture",
        }
    )
    return env


def load_valid_config() -> dict:
    return load_config_dir("deploy/docker/config", env=valid_env())


def load_fixture_config() -> dict:
    config = load_config_dir("tests/fixtures/config_valid", env=fixture_env())
    config["portainer_expected"]["fixture_actual"] = {"sites": copy.deepcopy(PORTAINER_SITES)}
    config["doctor"]["doctor"]["fixture_actual"] = {"sites": copy.deepcopy(DOCTOR_SITES)}
    config["rabbitmq_expected"]["fixture_actual"] = {"sites": copy.deepcopy(RABBITMQ_SITES)}
    config["recording"]["fixture_actual"] = copy.deepcopy(RECORDING_ACTUAL)
    config["servers"]["fixture_actual"] = {"sites": copy.deepcopy(INFRASTRUCTURE_SITES)}
    return config


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


PORTAINER_SERVICE = {
    "id": "svc1",
    "name": "recording-gateway",
    "stack": "recording",
    "desired_replicas": 3,
    "running_replicas": 3,
    "healthy_replicas": 3,
    "health": {
        "available": True,
        "source": "fixture",
        "definition": "fixture-provided healthy replica count",
    },
    "image": "example/recording-gateway:fixture",
    "service_mode": "replicated",
    "service_state": "running",
    "task_states": [
        {
            "id": f"task{idx}",
            "desired_state": "running",
            "current_state": "running",
            "health": "healthy",
        }
        for idx in range(1, 4)
    ],
    "failed_tasks": 0,
    "rejected_tasks": 0,
    "restarting_tasks": 0,
    "starting_tasks": 0,
}

PORTAINER_SITES = {
    "site1": {
        "environment_type": "docker_swarm",
        "api": {"source": "fixture", "api_contract": "fixture"},
        "services": [{**PORTAINER_SERVICE, "site": "site1"}],
    },
    "site2": {
        "environment_type": "docker_swarm",
        "api": {"source": "fixture", "api_contract": "fixture"},
        "services": [{**PORTAINER_SERVICE, "site": "site2"}],
    },
}

DOCTOR_SITES = {
    site: {"services": {service: {"healthy": True} for service in DOCTOR_SERVICES}}
    for site in ("site1", "site2")
}

RABBITMQ_QUEUE = {
    "vhost": "/",
    "name": "recording.events",
    "ready": 0,
    "unacked": 0,
    "total": 0,
    "checks_performed": 1,
    "snapshots": [{"check": 1, "ready": 0, "unacked": 0, "total": 0}],
}

RABBITMQ_NODE = {
    "resource_states": {
        "file_descriptors": "green",
        "socket_descriptors": "green",
        "erlang_processes": "green",
        "disk_space": "green",
    },
    "raw_resource_metrics": {
        "fd_used": 10,
        "fd_total": 1000,
        "sockets_used": 5,
        "sockets_total": 1000,
        "proc_used": 50,
        "proc_total": 100000,
        "disk_free_alarm": False,
    },
}

RABBITMQ_SITES = {
    "site1": {
        "queues": [copy.deepcopy(RABBITMQ_QUEUE)],
        "nodes": [{**copy.deepcopy(RABBITMQ_NODE), "name": "rabbit@fixture1"}],
    },
    "site2": {
        "queues": [copy.deepcopy(RABBITMQ_QUEUE)],
        "nodes": [{**copy.deepcopy(RABBITMQ_NODE), "name": "rabbit@fixture2"}],
    },
}

RECORDING_ACTUAL = {
    "selected_device": {
        "id": "fixture-device-1",
        "name": "Fixture Device",
        "recording": False,
        "created": False,
        "deleted": False,
    },
    "device_selection": {"success": True},
    "pre_start_verification": {"success": True, "recording": False},
    "observations": {
        "baseline": {
            "site1_webapp": {"success": True, "count": 2},
            "site2_webapp": {"success": True, "count": 3},
            "site1_server": {"success": True, "count": 4},
            "site2_server": {"success": True, "count": 5},
        },
        "after_start": {
            "site1_webapp": {"success": True, "count": 3},
            "site2_webapp": {"success": True, "count": 4},
            "site1_server": {"success": True, "count": 5},
            "site2_server": {"success": True, "count": 6},
        },
        "after_stop": {
            "site1_webapp": {"success": True, "count": 2},
            "site2_webapp": {"success": True, "count": 3},
            "site1_server": {"success": True, "count": 4},
            "site2_server": {"success": True, "count": 5},
        },
    },
    "start_action": {"success": True, "device_id": "fixture-device-1"},
    "stop_action": {"success": True, "device_id": "fixture-device-1"},
    "cleanup": {"success": True, "complete": True, "selected_device_recording": False},
}

INFRASTRUCTURE_SITES = {
    "site1": {
        "servers": {
            "srv1": {
                "reachable": True,
                "df": "Filesystem Size Used Avail Use% Mounted on\n/dev/sda1 100G 20G 80G 20% /",
                "chrony": {
                    "synchronized": True,
                    "source": "fixture-ntp",
                    "timezone": "UTC",
                    "offset": 0.1,
                },
            }
        }
    },
    "site2": {
        "servers": {
            "srv2": {
                "reachable": True,
                "df": "Filesystem Size Used Avail Use% Mounted on\n/dev/sda1 100G 20G 80G 20% /",
                "chrony": {
                    "synchronized": True,
                    "source": "fixture-ntp",
                    "timezone": "UTC",
                    "offset": 0.1,
                },
            }
        }
    },
}
