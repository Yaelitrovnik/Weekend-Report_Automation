from __future__ import annotations

import unittest
from pathlib import Path


class DockerConfigTests(unittest.TestCase):
    def test_dockerfile_uses_expected_python_base_image(self):
        dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
        self.assertIn("FROM weekend-report-runtime-base:python314", dockerfile)
        self.assertNotIn("FROM python:latest", dockerfile)

    def test_dockerfile_copies_code_without_deployment_config(self):
        dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
        self.assertIn("COPY app /app/app", dockerfile)
        self.assertIn("COPY scripts /app/scripts", dockerfile)
        self.assertNotIn("COPY . /app", dockerfile)
        self.assertNotIn("COPY config", dockerfile)
        self.assertNotIn("COPY deploy", dockerfile)

    def test_production_compose_uses_configurable_loaded_image(self):
        compose = Path("deploy/docker/compose.yml").read_text(encoding="utf-8")
        self.assertNotIn("build:", compose)
        self.assertGreaterEqual(
            compose.count("image: ${WEEKEND_REPORT_IMAGE:-weekend-report:local}"),
            2,
        )

    def test_production_compose_externalizes_runtime_configuration(self):
        compose = Path("deploy/docker/compose.yml").read_text(encoding="utf-8")
        for env_file in (
            "./env/app.env",
            "./env/portainer.env",
            "./env/doctor.env",
            "./env/rabbitmq.env",
            "./env/recording.env",
            "./env/infrastructure.env",
            "./env/splunk.env",
        ):
            self.assertIn(env_file, compose)
        self.assertIn("./config/rules.yml:/app/config/rules.yml:ro", compose)
        self.assertIn("./secrets:/app/secrets:ro", compose)
        self.assertNotIn("WEEKEND_REPORT_CONFIG_DIR:", compose)
        self.assertNotIn("WEEKEND_REPORT_EVIDENCE_ROOT:", compose)
        self.assertNotIn("<TBD>", compose)
        self.assertNotIn("env.example", compose)
        self.assertNotIn("PORTAINER_SITE1_TOKEN:", compose)
        self.assertNotIn("RABBITMQ_SITE1_PASSWORD:", compose)
        self.assertNotIn("WEEKEND_REPORT_CSRF_TOKEN", compose)

    def test_production_worker_has_outbound_network_access(self):
        import yaml

        compose = yaml.safe_load(Path("deploy/docker/compose.yml").read_text(encoding="utf-8"))
        self.assertIn("egress", compose["services"]["worker"]["networks"])
        egress = compose["networks"].get("egress") or {}
        self.assertFalse(egress.get("internal", False))
        self.assertTrue(compose["networks"]["backend"]["internal"])

    def test_mode_overrides_keep_expected_network_and_tls_behavior(self):
        direct = Path("deploy/docker/compose.direct.yml").read_text(encoding="utf-8")
        proxy = Path("deploy/docker/compose.proxy.yml").read_text(encoding="utf-8")
        self.assertIn("./tls:/app/tls:ro", direct)
        self.assertIn('"127.0.0.1:8080:8080"', proxy)

    def test_redundant_production_override_is_removed(self):
        self.assertFalse(Path("deploy/docker/compose.prod.yml").exists())

    def test_deployment_rules_file_is_the_single_runtime_policy_source(self):
        self.assertFalse(Path("config/rules.yml").exists())
        self.assertTrue(Path("deploy/docker/config/rules.yml").is_file())

    def test_environment_examples_are_split_templates_only(self):
        root = Path(".env.example").read_text(encoding="utf-8")
        compose = Path("deploy/docker/.env.example").read_text(encoding="utf-8")
        self.assertIn("WEEKEND_REPORT_CONFIG_DIR=deploy/docker/config", root)
        self.assertIn("WEEKEND_REPORT_IMAGE=weekend-report:local", compose)
        self.assertNotIn("PORTAINER_SITE1_TOKEN", root)
        self.assertNotIn("PORTAINER_SITE1_TOKEN", compose)

        env_dir = Path("deploy/docker/env")
        examples = {
            "app.env.example": "WEEKEND_REPORT_APP_VERSION=<TBD>",
            "portainer.env.example": "PORTAINER_SITE1_TOKEN=<TBD>",
            "doctor.env.example": "DOCTOR_SITE1_API_URL=<TBD>",
            "rabbitmq.env.example": "RABBITMQ_SITE1_PASSWORD=<TBD>",
            "recording.env.example": "RECORDING_MANAGER_WEBAPP_URL=<TBD>",
            "infrastructure.env.example": "SSH_PRIVATE_KEY_PATH=<TBD>",
            "splunk.env.example": "SPLUNK_DASHBOARD_COUNT=<TBD>",
        }
        for filename, expected in examples.items():
            text = (env_dir / filename).read_text(encoding="utf-8")
            self.assertIn(expected, text)
            self.assertNotIn("WEEKEND_REPORT_APP_VERSION=1.0.1", text)
            self.assertNotIn("WR-20260812-01", text)


if __name__ == "__main__":
    unittest.main()
