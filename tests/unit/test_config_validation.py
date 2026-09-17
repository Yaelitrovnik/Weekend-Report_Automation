from __future__ import annotations

import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config.loader import ConfigError, config_hash, load_config_dir, load_env_files
from app.config.validation import validate_config
from app.database.repository import Repository
from app.domain import CheckStatus
from app.evidence.manager import EvidenceManager
from app.orchestrator.run_context import RunContext
from app.validators.portainer import PortainerValidator
from tests.fixtures.config_factory import load_fixture_config, load_valid_config, valid_env


class ConfigValidationTests(unittest.TestCase):
    def test_default_config_blocks_missing_environment(self):
        report = validate_config(load_config_dir("deploy/docker/config", env={}))
        self.assertFalse(report.ok)
        self.assertTrue(any("sites.sites" in line for line in report.lines()))
        self.assertTrue(any("splunk_dashboards.dashboards" in line for line in report.lines()))

    def test_env_configuration_loading(self):
        config = load_valid_config()
        self.assertEqual(config["sites"]["sites"][0]["id"], "site1")
        self.assertEqual(
            config["portainer_expected"]["sites"]["site1"]["connection"]["url"],
            "https://portainer-site1.example.invalid",
        )
        self.assertEqual(
            config["rabbitmq_expected"]["connections"]["site2"]["user"],
            "fixture-user-2",
        )
        self.assertEqual(
            config["recording"]["manager"]["url"],
            "https://recording-manager.example.invalid",
        )

    def test_fixture_config_is_valid(self):
        report = validate_config(load_fixture_config(), production_preflight=False)
        self.assertTrue(report.ok, report.lines())

    def test_missing_required_env_variables_are_reported_without_values(self):
        env = valid_env()
        env["PORTAINER_SITE1_TOKEN"] = ""
        env["RABBITMQ_SITE2_PASSWORD"] = ""
        report = validate_config(load_config_dir("deploy/docker/config", env=env))
        lines = report.lines()
        self.assertFalse(report.ok)
        self.assertTrue(
            any("portainer_expected.sites.site1.connection.auth.token" in line for line in lines)
        )
        self.assertTrue(
            any("rabbitmq_expected.connections.site2.password" in line for line in lines)
        )
        self.assertFalse(any("fixture-password" in line for line in lines))

    def test_rabbitmq_custom_ca_is_valid(self):
        env = valid_env()
        env["RABBITMQ_SITE1_TLS_VERIFY"] = "custom_ca"
        env["RABBITMQ_SITE1_CA_FILE"] = "/app/secrets/rabbitmq-site1-ca.pem"

        config = load_config_dir(
            "deploy/docker/config",
            env=env,
        )

        report = validate_config(
            config,
            production_preflight=True,
        )

        rabbitmq_tls_errors = [
            line
            for line in report.lines()
            if "rabbitmq_expected.connections.site1" in line
            and (
                "tls_verify" in line
                or "ca_file" in line
            )
        ]

        self.assertEqual(
            rabbitmq_tls_errors,
            [],
            rabbitmq_tls_errors,
        )

        self.assertEqual(
            config["rabbitmq_expected"]["connections"]["site1"]["tls_verify"],
            "custom_ca",
        )
        self.assertEqual(
            config["rabbitmq_expected"]["connections"]["site1"]["ca_file"],
            "/app/secrets/rabbitmq-site1-ca.pem",
        )

    def test_rabbitmq_custom_ca_requires_ca_file_in_production(self):
        env = valid_env()
        env["RABBITMQ_SITE1_TLS_VERIFY"] = "custom_ca"
        env["RABBITMQ_SITE1_CA_FILE"] = ""

        config = load_config_dir(
            "deploy/docker/config",
            env=env,
        )

        report = validate_config(
            config,
            production_preflight=True,
        )

        self.assertFalse(report.ok)

        self.assertTrue(
            any(
                "rabbitmq_expected.connections.site1.ca_file" in line
                for line in report.lines()
            ),
            report.lines(),
        )

    def test_rabbitmq_tls_verify_false_is_rejected(self):
        env = valid_env()
        env["RABBITMQ_SITE1_TLS_VERIFY"] = "false"

        config = load_config_dir(
            "deploy/docker/config",
            env=env,
        )

        report = validate_config(
            config,
            production_preflight=False,
        )

        self.assertFalse(report.ok)

        self.assertTrue(
            any(
                "rabbitmq_expected.connections.site1.tls_verify" in line
                and "verify=false is not allowed" in line
                for line in report.lines()
            ),
            report.lines(),
        )

    def test_malformed_count_env_values_raise_config_error(self):
        env = valid_env()
        env["SITE1_SERVER_COUNT"] = "many"
        with self.assertRaises(ConfigError):
            load_config_dir("deploy/docker/config", env=env)

    def test_doctor_service_inventory_is_not_configured_from_env(self):
        config = load_valid_config()
        self.assertNotIn("expected_services", config["doctor"]["doctor"])

    def test_infrastructure_server_list_is_parsed_from_numbered_env(self):
        config = load_valid_config()
        site1_servers = config["servers"]["sites"]["site1"]["servers"]
        server = site1_servers[0]
        self.assertEqual(server["id"], "srv1")
        self.assertEqual(server["hostname"], "fixture1")
        self.assertEqual(server["ssh_port"], 22)
        self.assertTrue(server["required"])
        self.assertEqual(
            server["filesystems"],
            [
                {
                    "path": "/",
                    "command": "df -h /",
                    "warning_percent": 70,
                    "critical_percent": 80,
                    "required": True,
                }
            ],
        )
        self.assertEqual(server["chrony"][0]["timezone"], "UTC")
        self.assertEqual(server["chrony"][0]["source"], "fixture-ntp")
        self.assertEqual(server["chrony"][0]["warning_offset"], 0.5)
        self.assertEqual(server["chrony"][0]["critical_offset"], 1.0)
        self.assertNotIn("nfs_mounts", server)

    def test_infrastructure_uses_strict_private_key_ssh(self):
        config = load_valid_config()
        self.assertEqual(
            config["servers"]["ssh"]["auth"],
            "private_key",
        )
        self.assertEqual(
            config["servers"]["ssh"]["host_key_policy"],
            "strict",
        )
        self.assertEqual(
            config["servers"]["ssh"]["private_key_path"],
            "/run/secrets/fixture-key",
        )
        self.assertEqual(
            config["servers"]["ssh"]["known_hosts_path"],
            "/run/secrets/fixture-known-hosts",
        )

    def test_infrastructure_rejects_non_strict_host_key_policy(self):
        config = load_valid_config()
        config["servers"]["ssh"]["host_key_policy"] = "accept-new"
        report = validate_config(
            config,
            production_preflight=False,
        )
        self.assertFalse(report.ok)
        self.assertTrue(
            any(
                "servers.ssh.host_key_policy" in line
                and "must be strict" in line
                for line in report.lines()
            ),
            report.lines(),
        )

    def test_infrastructure_rejects_removed_nfs_configuration(self):
        config = load_valid_config()
        server = config["servers"]["sites"]["site1"]["servers"][0]
        server["nfs_mounts"] = [
            {
                "path": "/mnt/example",
            }
        ]
        report = validate_config(
            config,
            production_preflight=False,
        )
        self.assertFalse(report.ok)
        self.assertTrue(
            any(
                "nfs_mounts" in line
                and "NFS validation was removed" in line
                for line in report.lines()
            ),
            report.lines(),
        )

    def test_splunk_dashboards_are_parsed_from_numbered_env(self):
        config = load_valid_config()
        dashboards = config["splunk_dashboards"]["dashboards"]
        self.assertEqual(
            [dashboard["id"] for dashboard in dashboards],
            [
                "system_health",
                "recording",
                "infrastructure",
                "errors",
            ],
        )
        self.assertEqual(
            [dashboard["required_review"] for dashboard in dashboards],
            [True, True, True, True],
        )
        self.assertEqual(
            [dashboard["note_required"] for dashboard in dashboards],
            [False, False, False, False],
        )
        self.assertEqual(
            [dashboard["order"] for dashboard in dashboards],
            [1, 2, 3, 4],
        )

    def test_splunk_dashboard_review_policy_can_be_overridden_per_dashboard(self):
        env = valid_env()
        env["SPLUNK_DASHBOARD_1_REQUIRED_REVIEW"] = "false"
        env["SPLUNK_DASHBOARD_1_NOTE_REQUIRED"] = "true"
        env["SPLUNK_DASHBOARD_1_ORDER"] = "10"
        env["SPLUNK_DASHBOARD_2_REQUIRED_REVIEW"] = "true"
        env["SPLUNK_DASHBOARD_2_NOTE_REQUIRED"] = "false"
        env["SPLUNK_DASHBOARD_2_ORDER"] = "20"
        config = load_config_dir(
            "deploy/docker/config",
            env=env,
        )
        dashboards = config["splunk_dashboards"]["dashboards"]
        self.assertEqual(
            dashboards[0]["id"],
            "system_health",
        )
        self.assertFalse(
            dashboards[0]["required_review"],
        )
        self.assertTrue(
            dashboards[0]["note_required"],
        )
        self.assertEqual(
            dashboards[0]["order"],
            10,
        )
        self.assertEqual(
            dashboards[1]["id"],
            "recording",
        )
        self.assertTrue(
            dashboards[1]["required_review"],
        )
        self.assertFalse(
            dashboards[1]["note_required"],
        )
        self.assertEqual(
            dashboards[1]["order"],
            20,
        )
        report = validate_config(
            config,
            production_preflight=False,
        )
        self.assertTrue(
            report.ok,
            report.lines(),
        )

    def test_parity_sites_come_from_runtime_env_not_rules(self):
        env = valid_env()
        env["SITE1_ID"] = "primary"
        env["SITE2_ID"] = "secondary"
        config = load_config_dir("deploy/docker/config", env=env)
        report = validate_config(config, production_preflight=False)
        self.assertTrue(report.ok, report.lines())
        self.assertNotIn("sites", config["rules"]["parity"][0])
        self.assertEqual(
            [site["id"] for site in config["sites"]["sites"]],
            ["primary", "secondary"],
        )

    def test_removed_portainer_image_reference_and_comparison_are_not_required(self):
        config = load_valid_config()
        report = validate_config(config, production_preflight=False)
        self.assertTrue(report.ok, report.lines())
        rendered = str(config["portainer_expected"])
        self.assertNotIn("image.reference", rendered)
        self.assertNotIn("image.comparison", rendered)
        self.assertNotIn("image_comparison", rendered)

    def test_rules_yml_controls_portainer_task_policy(self):
        config = load_fixture_config()
        actual = copy.deepcopy(config["portainer_expected"]["fixture_actual"]["sites"])
        actual["site1"]["services"][0]["starting_tasks"] = 1
        with tempfile.TemporaryDirectory() as tmp:
            repository = Repository("sqlite:///:memory:")
            try:
                ctx = RunContext(
                    "WR-20260811-000000",
                    config,
                    repository,
                    EvidenceManager(Path(tmp)),
                )
                result = [
                    item
                    for item in PortainerValidator().validate(
                        {"sites": actual, "errors": []},
                        config,
                        ctx,
                    )
                    if item.check_id == "portainer.service.task_state"
                    and item.site == "site1"
                ][0]
                self.assertEqual(result.status, CheckStatus.WARNING)
                config["rules"]["portainer"]["task_state_policy"]["starting"] = "FAIL"
                result = [
                    item
                    for item in PortainerValidator().validate(
                        {"sites": actual, "errors": []},
                        config,
                        ctx,
                    )
                    if item.check_id == "portainer.service.task_state"
                    and item.site == "site1"
                ][0]
                self.assertEqual(result.status, CheckStatus.FAIL)
            finally:
                repository.close()

    def test_env_file_loader_supports_config_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "app.env"
            env_file.write_text("SITE_COUNT=2\nSITE1_ID=site1\nSITE2_ID=site2\n", encoding="utf-8")
            target: dict[str, str] = {}
            load_env_files([env_file], environ=target)
        self.assertEqual(target["SITE_COUNT"], "2")
        self.assertEqual(target["SITE2_ID"], "site2")

    def test_production_traceability_runtime_values_are_required(self):
        env = valid_env()
        env["WEEKEND_REPORT_APP_VERSION"] = "<TBD>"
        env["WEEKEND_REPORT_BUILD_ID"] = ""
        with patch.dict(os.environ, env, clear=True):
            report = validate_config(load_config_dir("deploy/docker/config", env=env))
        self.assertFalse(report.ok)
        self.assertTrue(any("WEEKEND_REPORT_APP_VERSION" in line for line in report.lines()))
        self.assertTrue(any("WEEKEND_REPORT_BUILD_ID" in line for line in report.lines()))

    def test_config_hash_redacts_secrets_but_tracks_policy(self):
        config = load_valid_config()
        secret_changed = copy.deepcopy(config)
        secret_changed["portainer_expected"]["sites"]["site1"]["connection"]["auth"][
            "token"
        ] = "different-token"
        policy_changed = copy.deepcopy(config)
        policy_changed["servers"]["ssh"]["host_key_policy"] = "accept-new"

        self.assertEqual(
            config_hash("config", effective_config=config),
            config_hash("config", effective_config=secret_changed),
        )
        self.assertNotEqual(
            config_hash("config", effective_config=config),
            config_hash("config", effective_config=policy_changed),
        )
    def test_manual_db_check_is_loaded_from_env(self):
        config = load_valid_config()
        self.assertEqual(
            config["manual_db_check"],
            {
                "enabled": True,
                "display_name": "Database Synchronization Check",
                "script_path": (
                    "C:\\Scripts\\DatabaseSync\\database_sync_check.ps1"
                ),
                "required": True,
            },
        )

    def test_manual_db_check_rejects_invalid_boolean_env(self):
        env = valid_env()
        env["WEEKEND_REPORT_DB_MANUAL_CHECK_ENABLED"] = "maybe"
        with self.assertRaises(ConfigError):
            load_config_dir(
                "deploy/docker/config",
                env=env,
            )

    def test_manual_db_check_requires_display_name_when_enabled(self):
        env = valid_env()
        env["WEEKEND_REPORT_DB_MANUAL_CHECK_DISPLAY_NAME"] = ""
        config = load_config_dir(
            "deploy/docker/config",
            env=env,
        )
        report = validate_config(
            config,
            production_preflight=False,
        )
        self.assertFalse(report.ok)
        self.assertTrue(
            any(
                "manual_db_check.display_name" in line
                for line in report.lines()
            )
        )

    def test_manual_db_check_requires_absolute_ps1_path_when_enabled(self):
        env = valid_env()
        env["WEEKEND_REPORT_DB_MANUAL_CHECK_SCRIPT_PATH"] = (
            "database_sync_check.ps1"
        )
        config = load_config_dir(
            "deploy/docker/config",
            env=env,
        )
        report = validate_config(
            config,
            production_preflight=False,
        )
        self.assertFalse(report.ok)
        self.assertTrue(
            any(
                "manual_db_check.script_path" in line
                for line in report.lines()
            )
        )

    def test_manual_db_check_required_cannot_be_true_when_disabled(self):
        env = valid_env()
        env["WEEKEND_REPORT_DB_MANUAL_CHECK_ENABLED"] = "false"
        env["WEEKEND_REPORT_DB_MANUAL_CHECK_REQUIRED"] = "true"
        config = load_config_dir(
            "deploy/docker/config",
            env=env,
        )
        report = validate_config(
            config,
            production_preflight=False,
        )
        self.assertFalse(report.ok)
        self.assertTrue(
            any(
                "manual_db_check.required" in line
                for line in report.lines()
            )
        )
    def test_manual_db_check_requires_ps1_extension(self):
        env = valid_env()
        env["WEEKEND_REPORT_DB_MANUAL_CHECK_SCRIPT_PATH"] = (
            "C:\\Scripts\\DatabaseSync\\database_sync_check.txt"
        )
        config = load_config_dir(
            "deploy/docker/config",
            env=env,
        )
        report = validate_config(
            config,
            production_preflight=False,
        )
        self.assertFalse(report.ok)
        self.assertTrue(
            any(
                "must reference a .ps1 file" in line
                for line in report.lines()
            )
        )


if __name__ == "__main__":
    unittest.main()
