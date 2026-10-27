from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config.loader import ConfigError, _sanitize_for_hash, load_config_dir, parse_env_file
from app.config.validation import validate_config
from tests.fixtures.config_factory import load_fixture_config, valid_env


class SSHConfigTests(unittest.TestCase):
    def report(self, env):
        with patch.dict("os.environ", env, clear=True):
            return validate_config(load_config_dir("deploy/docker/config", env=env))

    def test_production_password_config_needs_no_key(self):
        env = valid_env()
        self.assertNotIn("SSH_PRIVATE_KEY_PATH", env)
        report = self.report(env)
        self.assertTrue(report.ok, report.lines())

    def test_required_production_fields_and_placeholders(self):
        for name, field in (("SSH_USERNAME", "username"), ("SSH_PASSWORD", "password"),
                            ("SSH_KNOWN_HOSTS_PATH", "known_hosts_path")):
            for value in (None, "", " ", "<TBD>", "<fake-secret-placeholder>", "UNKNOWN"):
                with self.subTest(name=name, kind=value):
                    env = valid_env()
                    if value is None:
                        del env[name]
                    else:
                        env[name] = value
                    report = self.report(env)
                    self.assertFalse(report.ok)
                    self.assertTrue(any(
                        issue.path == f"servers.ssh.{field}" for issue in report.errors
                    ))
                    if name == "SSH_PASSWORD":
                        self.assertNotIn("fake-secret-placeholder", str(report.lines()))

    def test_fixture_settings_cannot_bypass_production(self):
        for field, values in (
            ("auth", ("private_key", "fixture", "", "<TBD>")),
            ("host_key_policy", ("fixture", "accept-new", "no", "", "<TBD>")),
        ):
            for value in values:
                config = load_fixture_config()
                config["servers"]["ssh"][field] = value
                report = validate_config(config)
                self.assertTrue(any(
                    issue.path == f"servers.ssh.{field}" for issue in report.errors
                ))
        report = validate_config(load_fixture_config(), production_preflight=False)
        self.assertTrue(report.ok, report.lines())

    def test_fixture_policy_requires_actual_fixture_data(self):
        config = load_fixture_config()
        del config["servers"]["fixture_actual"]
        report = validate_config(config, production_preflight=False)
        self.assertTrue(any(issue.path == "servers.ssh.auth" for issue in report.errors))

    def test_stale_key_env_is_rejected_without_value_even_when_empty(self):
        for value in ("", "/fake/obsolete-key", "fake-secret-obsolete-value"):
            env = valid_env() | {"SSH_PRIVATE_KEY_PATH": value}
            with self.assertRaisesRegex(ConfigError, "SSH_PRIVATE_KEY_PATH is obsolete") as raised:
                load_config_dir("deploy/docker/config", env=env)
            self.assertNotIn("fake-secret-obsolete-value", str(raised.exception))

    def test_stale_key_config_is_rejected(self):
        config = load_fixture_config()
        config["servers"]["ssh"]["private_key_path"] = "/fake/obsolete-key"
        report = validate_config(config, production_preflight=False)
        self.assertTrue(any(
            issue.path == "servers.ssh.private_key_path" for issue in report.errors
        ))

    def test_password_hash_sanitization_preserved(self):
        env = valid_env()
        config = load_config_dir("deploy/docker/config", env=env)
        changed = load_config_dir("deploy/docker/config", env=env | {
            "SSH_PASSWORD": "different-fake-password-for-tests-only",
        })
        self.assertEqual(config["_config_hash"], changed["_config_hash"])
        self.assertNotIn(env["SSH_PASSWORD"], json.dumps(_sanitize_for_hash(config)))
        self.assertEqual(_sanitize_for_hash({"SSH_PASSWORD": env["SSH_PASSWORD"]}),
                         {"SSH_PASSWORD": "<REDACTED>"})

    def test_password_whitespace_and_literal_env_characters_are_preserved(self):
        fake = ' fake-test-$VALUE # "literal" \\tail '
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.env"
            path.write_text(f"SSH_PASSWORD='{fake}'\n", encoding="utf-8")
            parsed = parse_env_file(path)
        config = load_config_dir("deploy/docker/config", env=valid_env() | parsed)
        self.assertEqual(config["servers"]["ssh"]["password"], fake)
