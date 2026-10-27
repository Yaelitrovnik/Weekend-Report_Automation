from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.config.validation import ValidationReport, _validate_runtime_environment


def messages(report: ValidationReport, path: str) -> list[str]:
    return [issue.message for issue in report.errors if issue.path == path]


class SecurityConfigValidationTests(unittest.TestCase):
    def validate(self, env: dict[str, str]) -> ValidationReport:
        report = ValidationReport()
        with patch.dict(os.environ, env, clear=True):
            _validate_runtime_environment(
                {"_config_hash": "fixture-config-hash"},
                report,
                production_preflight=True,
            )
        return report

    def base_env(self) -> dict[str, str]:
        return {
            "WEEKEND_REPORT_APP_VERSION": "v0.0.0-test",
            "WEEKEND_REPORT_BUILD_ID": "test-build",
            "WEEKEND_REPORT_CSRF_SIGNING_KEY": "csrf-secret",
            "WEEKEND_REPORT_CSRF_TTL_SECONDS": "3600",
        }

    def test_production_preflight_requires_csrf_signing_key(self):
        env = self.base_env()
        del env["WEEKEND_REPORT_CSRF_SIGNING_KEY"]
        report = self.validate(env)
        self.assertTrue(messages(report, "runtime.csrf"))

    def test_csrf_ttl_must_be_positive_integer(self):
        for value in ("0", "-1", "not-a-number"):
            with self.subTest(value=value):
                env = self.base_env()
                env["WEEKEND_REPORT_CSRF_TTL_SECONDS"] = value
                report = self.validate(env)
                self.assertTrue(messages(report, "runtime.csrf"))

    def test_valid_security_configuration(self):
        report = self.validate(self.base_env())
        self.assertEqual(messages(report, "runtime.csrf"), [])
        self.assertEqual(messages(report, "runtime.traceability"), [])


if __name__ == "__main__":
    unittest.main()
