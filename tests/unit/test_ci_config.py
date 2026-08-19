from __future__ import annotations

import unittest
from pathlib import Path

import yaml


class CIConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.github_quality = Path(".github/workflows/quality-gates.yml")
        self.github_image = Path(".github/workflows/build-image.yml")
        self.gitlab_root = Path(".gitlab-ci.yml")
        self.gitlab_quality = Path(".gitlab/ci/quality.yml")
        self.gitlab_image = Path(".gitlab/ci/image.yml")
        self.ci_compose = Path("deploy/docker/compose.ci.yml")

    def test_ci_yaml_files_parse(self):
        for path in (
            self.github_quality,
            self.github_image,
            self.gitlab_root,
            self.gitlab_quality,
            self.gitlab_image,
            self.ci_compose,
        ):
            self.assertTrue(path.exists(), path)
            self.assertIsNotNone(yaml.safe_load(path.read_text(encoding="utf-8")), path)

    def test_github_quality_exposes_each_release_blocking_gate(self):
        text = self.github_quality.read_text(encoding="utf-8")
        for gate in (
            "config-validation:",
            "ruff:",
            "mypy:",
            "unit-tests:",
            "integration-tests:",
            "contract-tests:",
            "postgres-concurrency:",
            "safe-e2e:",
            "dependency-audit:",
            "docker-compose-validate:",
        ):
            self.assertIn(gate, text)
        self.assertIn("workflow_call:", text)
        self.assertIn("postgres:16-alpine", text)
        self.assertNotIn("continue-on-error", text)

    def test_github_image_is_gated_and_smoked_before_export_or_publish(self):
        text = self.github_image.read_text(encoding="utf-8")
        self.assertIn("uses: ./.github/workflows/quality-gates.yml", text)
        self.assertIn("needs: quality", text)
        build = text.index("Build image only after all pre-image gates pass")
        smoke = text.index("Smoke-test the exact built image")
        export = text.index("Export verified offline image artifact")
        publish = text.index("Publish the exact verified image")
        self.assertLess(build, smoke)
        self.assertLess(smoke, export)
        self.assertLess(export, publish)
        self.assertNotIn("continue-on-error", text)

    def test_gitlab_image_needs_all_quality_jobs(self):
        text = self.gitlab_image.read_text(encoding="utf-8")
        for gate in (
            "config-validation",
            "ruff",
            "mypy",
            "unit-tests",
            "integration-tests",
            "contract-tests",
            "postgres-concurrency",
            "safe-e2e",
            "dependency-audit",
            "docker-compose-validate",
        ):
            self.assertIn(f"- {gate}", text)
        self.assertNotIn("allow_failure: true", text)
        self.assertLess(text.index("docker build"), text.index("image-smoke"))
        self.assertLess(text.index("image-smoke"), text.index("docker save"))
        self.assertLess(text.index("docker save"), text.index("WEEKEND_REPORT_PUBLISH_IMAGE"))

    def test_image_release_is_driven_by_tag_file(self):
        github_text = self.github_image.read_text(encoding="utf-8")
        gitlab_text = self.gitlab_image.read_text(encoding="utf-8")

        self.assertIn("paths:", github_text)
        self.assertIn("- TAG", github_text)
        self.assertNotIn("GITHUB_REF_NAME", github_text)
        self.assertNotIn("refs/tags/", github_text)

        self.assertIn("changes:", gitlab_text)
        self.assertIn("- TAG", gitlab_text)
        self.assertNotIn("CI_COMMIT_TAG", gitlab_text)

        self.assertIn("< TAG", github_text)
        self.assertIn("< TAG", gitlab_text)

    def test_ci_compose_uses_exact_image_and_fixture_config(self):
        text = self.ci_compose.read_text(encoding="utf-8")
        self.assertIn("WEEKEND_REPORT_CI_IMAGE", text)
        self.assertIn("/app/tests/fixtures/config_valid", text)
        self.assertNotIn("build:", text)
        self.assertIn("postgres:16-alpine", text)

    def test_ci_files_do_not_contain_production_integration_secrets(self):
        combined = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (
                self.github_quality,
                self.github_image,
                self.gitlab_root,
                self.gitlab_quality,
                self.gitlab_image,
            )
        )
        for forbidden in (
            "PORTAINER_SITE1_TOKEN",
            "PORTAINER_SITE2_TOKEN",
            "RABBITMQ_SITE1_PASSWORD",
            "RABBITMQ_SITE2_PASSWORD",
            "SSH_PRIVATE_KEY_PATH",
        ):
            self.assertNotIn(forbidden, combined)


if __name__ == "__main__":
    unittest.main()
