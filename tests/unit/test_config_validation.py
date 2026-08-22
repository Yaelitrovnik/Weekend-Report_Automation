from __future__ import annotations

import copy
import os
import unittest
from unittest.mock import patch

from app.config.loader import load_config_dir
from app.config.validation import validate_config


class ConfigValidationTests(unittest.TestCase):
    def test_default_config_blocks_unresolved_placeholders(self):
        report = validate_config(load_config_dir("config"))
        self.assertFalse(report.ok)
        self.assertTrue(any("<TBD>" in issue.message for issue in report.errors))

    def test_fixture_config_is_valid(self):
        report = validate_config(load_config_dir("tests/fixtures/config_valid"))
        self.assertTrue(report.ok, report.lines())

    def test_production_traceability_runtime_values_are_required(self):
        env = {
            "WEEKEND_REPORT_AUTH_MODE": "production",
            "WEEKEND_REPORT_APP_VERSION": "<TBD>",
            "WEEKEND_REPORT_BUILD_ID": "",
        }
        with patch.dict(os.environ, env):
            report = validate_config(load_config_dir("tests/fixtures/config_valid"))
        self.assertFalse(report.ok)
        self.assertTrue(any("WEEKEND_REPORT_APP_VERSION" in line for line in report.lines()))
        self.assertTrue(any("WEEKEND_REPORT_BUILD_ID" in line for line in report.lines()))

    def test_oidc_provider_requires_issuer_audience_and_jwks_url(self):
        env = {
            "WEEKEND_REPORT_AUTH_MODE": "production",
            "WEEKEND_REPORT_AUTH_PROVIDER": "oidc",
            "WEEKEND_REPORT_AUTHORIZED_REVIEWERS": "alice@example.invalid",
            "WEEKEND_REPORT_CSRF_SIGNING_KEY": "test-signing-key",
        }
        with patch.dict(os.environ, env, clear=False):
            for stale_var in (
                "WEEKEND_REPORT_AUTH_OIDC_ISSUER",
                "WEEKEND_REPORT_AUTH_OIDC_AUDIENCE",
                "WEEKEND_REPORT_AUTH_OIDC_JWKS_URL",
            ):
                os.environ.pop(stale_var, None)
            report = validate_config(load_config_dir("tests/fixtures/config_valid"))
        self.assertFalse(report.ok)
        self.assertTrue(
            any("WEEKEND_REPORT_AUTH_OIDC_ISSUER" in line for line in report.lines())
        )
        self.assertTrue(
            any("WEEKEND_REPORT_AUTH_OIDC_AUDIENCE" in line for line in report.lines())
        )
        self.assertTrue(
            any("WEEKEND_REPORT_AUTH_OIDC_JWKS_URL" in line for line in report.lines())
        )

    def test_oidc_provider_passes_preflight_when_fully_configured(self):
        env = {
            "WEEKEND_REPORT_AUTH_MODE": "production",
            "WEEKEND_REPORT_AUTH_PROVIDER": "oidc",
            "WEEKEND_REPORT_AUTH_OIDC_ISSUER": "https://issuer.invalid/",
            "WEEKEND_REPORT_AUTH_OIDC_AUDIENCE": "weekend-report",
            "WEEKEND_REPORT_AUTH_OIDC_JWKS_URL": "https://issuer.invalid/.well-known/jwks.json",
            "WEEKEND_REPORT_AUTHORIZED_REVIEWERS": "alice@example.invalid",
            "WEEKEND_REPORT_CSRF_SIGNING_KEY": "test-signing-key",
            "WEEKEND_REPORT_APP_VERSION": "test-version",
            "WEEKEND_REPORT_BUILD_ID": "test-build",
        }
        with patch.dict(os.environ, env):
            report = validate_config(load_config_dir("tests/fixtures/config_valid"))
        self.assertTrue(
            not any("runtime.auth" in line for line in report.lines()), report.lines()
        )

    def test_schema_reports_deep_wrong_expected_state_type(self):
        config = copy.deepcopy(load_config_dir("tests/fixtures/config_valid"))
        config["servers"]["sites"]["site1"]["servers"][0]["filesystems"][0][
            "warning_percent"
        ] = "eighty"

        report = validate_config(config)

        self.assertFalse(report.ok)
        self.assertTrue(
            any(
                issue.path
                == "servers.sites.site1.servers[0].filesystems[0].warning_percent"
                and issue.message == "must be a number"
                for issue in report.errors
            ),
            report.lines(),
        )

    def test_rabbitmq_threshold_keeps_legacy_source_path(self):
        config = copy.deepcopy(load_config_dir("tests/fixtures/config_valid"))
        config["rabbitmq_expected"]["defaults"]["queue"]["critical_messages"] = 10

        report = validate_config(config)

        self.assertTrue(
            any(
                issue.path
                == "rabbitmq_expected.defaults.queue.critical_messages"
                and issue.message == "critical threshold must be greater than warning threshold"
                for issue in report.errors
            ),
            report.lines(),
        )
    def test_rabbitmq_threshold_override_is_still_validated(self):
        config = copy.deepcopy(load_config_dir("tests/fixtures/config_valid"))
        config["rabbitmq_expected"]["sites"]["site1"]["overrides"] = {
            "queues": {
                "recording.events": {
                    "warning_messages": 20,
                    "critical_messages": 10,
                }
            }
        }

        report = validate_config(config)

        expected_path = (
            "rabbitmq_expected.sites.site1.overrides.queues."
            "recording.events.critical_messages"
        )
        self.assertTrue(
            any(
                issue.path == expected_path
                and issue.message == "critical threshold must be greater than warning threshold"
                for issue in report.errors
            ),
            report.lines(),
        )


if __name__ == "__main__":
    unittest.main()
