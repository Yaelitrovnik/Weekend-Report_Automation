from __future__ import annotations

import unittest
from pathlib import Path


class DockerConfigTests(unittest.TestCase):
    def test_compose_does_not_use_template_placeholders_as_runtime_secrets(self):
        compose = Path("deploy/docker/compose.yml").read_text(encoding="utf-8")
        self.assertNotIn("<TBD>", compose)
        self.assertNotIn("env.example", compose)
        self.assertNotIn("env_file", compose)
        self.assertIn("POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?", compose)
        self.assertIn("WEEKEND_REPORT_APP_VERSION: ${WEEKEND_REPORT_APP_VERSION:?", compose)
        self.assertIn("WEEKEND_REPORT_BUILD_ID: ${WEEKEND_REPORT_BUILD_ID:?", compose)
        self.assertIn(
            "WEEKEND_REPORT_CSRF_SIGNING_KEY: ${WEEKEND_REPORT_CSRF_SIGNING_KEY:?",
            compose,
        )
        self.assertIn("PORTAINER_SITE1_URL: ${PORTAINER_SITE1_URL:-}", compose)
        self.assertIn("PORTAINER_SITE1_TOKEN: ${PORTAINER_SITE1_TOKEN:-}", compose)
        self.assertIn("PORTAINER_SITE2_URL: ${PORTAINER_SITE2_URL:-}", compose)
        self.assertIn("PORTAINER_SITE2_TOKEN: ${PORTAINER_SITE2_TOKEN:-}", compose)
        self.assertNotIn("WEEKEND_REPORT_CSRF_TOKEN", compose)


if __name__ == "__main__":
    unittest.main()
