from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
