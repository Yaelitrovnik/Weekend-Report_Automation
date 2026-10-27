from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.collectors.infrastructure import InfrastructureCollector
from app.config.loader import load_config_dir
from app.config.validation import validate_config
from app.database.repository import Repository
from app.domain import CheckStatus
from app.evidence.manager import EvidenceManager
from app.executors.ssh import SSHExecutor, SSHResult
from app.orchestrator.aggregation import aggregate_status, module_summaries, site_summaries
from app.orchestrator.run_context import RunContext
from app.validators.infrastructure import InfrastructureValidator, parse_df
from tests.fixtures.config_factory import load_fixture_config, valid_env
from tests.fixtures.dynamic_sites import dynamic_config

COMMAND = "df -h | grep mapper"


def mapper_line(usage=40, mount="/data", device="/dev/mapper/vg-data"):
    return f"{device} 100G 40G 60G {usage}% {mount}\n"


class MapperFilesystemTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Repository("sqlite:///:memory:")
        self.addCleanup(self.repo.close)
        self.evidence = EvidenceManager(Path(tmp.name))
        self.config = load_fixture_config()

    def context(self, config):
        return RunContext("WR-20261020-000001", config, self.repo, self.evidence)

    def validate_output(self, output):
        actual = self.config["servers"]["fixture_actual"]
        actual["sites"]["site1"]["servers"]["srv1"].update(output)
        results = InfrastructureValidator().validate(
            actual, self.config, self.context(self.config)
        )
        return [
            r for r in results
            if r.site == "site1" and r.check_id.startswith("infrastructure.filesystem.")
        ]

    def collect_and_validate(self, exit_code=0, stdout="", stderr="", exception=None):
        config = load_fixture_config()
        del config["servers"]["fixture_actual"]
        ssh = Mock(spec=SSHExecutor)
        other_outputs = {
            "timedatectl show -p Timezone --value": "UTC",
            "chronyc tracking": (
                "System time : 0.001 seconds slow of NTP time\nLeap status : Normal"
            ),
            "chronyc sources -n": "^* fixture-ntp 1 6 377 12 +10us",
        }

        def run(**kwargs):
            command = kwargs["command"]
            if command == COMMAND:
                if exception:
                    raise exception
                return SSHResult(kwargs["host"], command, exit_code, stdout, stderr, 5)
            return SSHResult(kwargs["host"], command, 0, other_outputs[command], "", 5)

        ssh.run.side_effect = run
        context = self.context(config)
        actual = InfrastructureCollector(ssh).collect(context)
        results = InfrastructureValidator().validate(actual, config, context)
        return actual, results, ssh

    def test_mapper_threshold_boundaries(self):
        for usage, expected in (
            (40, CheckStatus.PASS),
            (69, CheckStatus.PASS),
            (70, CheckStatus.WARNING),
            (79, CheckStatus.WARNING),
            (80, CheckStatus.FAIL),
            (95, CheckStatus.FAIL),
        ):
            with self.subTest(usage=usage):
                results = self.validate_output({"df": mapper_line(usage)})
                self.assertEqual([r.status for r in results], [expected])
                self.assertEqual(results[0].actual, {
                    "filesystem": "/dev/mapper/vg-data",
                    "mountpoint": "/data",
                    "utilization_percent": usage,
                })
                self.assertEqual(results[0].target, "srv1")
                self.assertIn("/dev/mapper/vg-data mounted on /data", results[0].message)
                self.assertIn(f"{usage}%", results[0].message)

    def test_every_mapper_mount_is_independent(self):
        for mounts in (("/", "/var", "/data"), ("/var", "/app", "/arbitrary mount")):
            with self.subTest(mounts=mounts):
                raw = "".join(
                    mapper_line(40 + index, mount, f"/dev/mapper/vg-{index}")
                    for index, mount in enumerate(mounts)
                )
                results = self.validate_output({"df": raw})
                self.assertEqual([r.status for r in results], [CheckStatus.PASS] * 3)
                self.assertEqual([r.actual["mountpoint"] for r in results], list(mounts))
                self.assertEqual(len({r.actual["filesystem"] for r in results}), 3)

    def test_one_failed_mapper_is_not_masked_by_other_filesystems(self):
        results = self.validate_output({
            "df": mapper_line(40) + mapper_line(84, "/var/log", "/dev/mapper/vg-log")
        })
        self.assertEqual([r.status for r in results], [CheckStatus.PASS, CheckStatus.FAIL])
        self.assertEqual(aggregate_status(results, self.config), CheckStatus.FAIL)
        self.assertEqual(module_summaries(results, self.config)[0].status, CheckStatus.FAIL)

    def test_parser_preserves_devices_even_at_same_mountpoint(self):
        rows = parse_df(
            "\n" + mapper_line(40) + mapper_line(70, "/data", "/dev/mapper/other") + "\n"
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["filesystem"], "/dev/mapper/other")

    def test_malformed_output_including_mixed_valid_lines_is_error(self):
        for raw in (
            "not df output",
            "/dev/mapper/vg-data 100G 40G 60G ??? /data",
            "/dev/mapper/vg-data 100G 40G 60G 40%",
            "/dev/mapper/vg-data\n 100G 40G 60G 40% /data",
            "/dev/sda1 100G 40G 60G 40% /mapper",
            mapper_line() + "/dev/mapper/bad 100G",
            mapper_line() + "unexpected diagnostics",
        ):
            with self.subTest(raw=raw):
                results = self.validate_output({"df": raw})
                self.assertEqual([r.status for r in results], [CheckStatus.ERROR])
                self.assertIn("could not be parsed", results[0].message)

    def test_missing_output_is_error(self):
        for raw in (None, [], 0):
            with self.subTest(raw=raw):
                self.assertEqual(
                    self.validate_output({"df": raw})[0].status, CheckStatus.ERROR
                )

    def test_clean_empty_output_and_grep_exit_one_are_fail_not_unreachable(self):
        for exit_code in (0, 1):
            with self.subTest(exit_code=exit_code):
                actual, results, ssh = self.collect_and_validate(exit_code)
                for site in actual["sites"].values():
                    server = next(iter(site["servers"].values()))
                    self.assertTrue(server["reachable"])
                    self.assertNotIn("df_error", server)
                    self.assertEqual(server["commands"]["df"]["exit_code"], exit_code)
                filesystems = [r for r in results if ".filesystem." in r.check_id]
                self.assertEqual([r.status for r in filesystems], [CheckStatus.FAIL] * 2)
                self.assertTrue(all(
                    r.message == "No mapper filesystems were found" for r in filesystems
                ))
                self.assertTrue(all(
                    r.status == CheckStatus.PASS
                    for r in results if ".chrony." in r.check_id or ".ssh." in r.check_id
                ))
                self.assertEqual(ssh.run.call_count, 8)

    def test_command_errors_and_masked_upstream_diagnostics_remain_error(self):
        for exit_code, stdout, stderr in (
            (2, "", "grep: error"),
            (127, "", "grep: command not found"),
            (2, mapper_line(), ""),
            (1, "", "df: failed to read table"),
            (0, mapper_line(), "df: partial collection failed"),
            (1, mapper_line(), ""),
        ):
            with self.subTest(exit_code=exit_code, stderr=stderr):
                actual, results, _ = self.collect_and_validate(exit_code, stdout, stderr)
                self.assertTrue(actual["sites"]["site1"]["servers"]["srv1"]["reachable"])
                filesystems = [r for r in results if ".filesystem." in r.check_id]
                self.assertEqual([r.status for r in filesystems], [CheckStatus.ERROR] * 2)
                self.assertTrue(all(r.actual["df"] == stdout for r in filesystems))
                self.assertTrue(all(
                    r.status == CheckStatus.PASS for r in results if ".chrony." in r.check_id
                ))

    def test_ssh_failures_remain_error(self):
        for exit_code, stderr, exception in (
            (255, "Connection refused", None),
            (255, "SSH authentication failed", None),
            (255, "Host key verification failed", None),
            (None, "SSH command timed out", None),
            (None, "", RuntimeError("SSH transport unavailable")),
        ):
            with self.subTest(stderr=stderr, exception=exception):
                _, results, ssh = self.collect_and_validate(
                    exit_code, stderr=stderr, exception=exception
                )
                self.assertEqual([r.status for r in results], [CheckStatus.ERROR] * 2)
                self.assertTrue(all(r.check_id == "infrastructure.ssh.reachable" for r in results))
                self.assertEqual(ssh.run.call_count, 2)

    def test_real_executor_timeout_is_normalized_without_contacting_ssh(self):
        config = load_fixture_config()
        del config["servers"]["fixture_actual"]
        context = self.context(config)
        with patch("app.executors.ssh.paramiko.SSHClient") as factory:
            factory.return_value.connect.side_effect = TimeoutError("fake-timeout")
            actual = InfrastructureCollector().collect(context)
        results = InfrastructureValidator().validate(actual, config, context)
        self.assertEqual([r.status for r in results], [CheckStatus.ERROR] * 2)
        self.assertEqual(factory.return_value.connect.call_count, 2)
        factory.return_value.exec_command.assert_not_called()

    def test_exception_text_with_password_is_not_in_collector_evidence(self):
        secret = valid_env()["SSH_PASSWORD"]
        actual, results, _ = self.collect_and_validate(exception=RuntimeError(secret))
        self.assertNotIn(secret, repr(actual))
        self.assertNotIn(secret, repr(results))

    def test_live_collection_keeps_multiple_results_and_chrony_unchanged(self):
        raw = mapper_line(40, "/") + mapper_line(72, "/var", "/dev/mapper/vg-var")
        actual, results, ssh = self.collect_and_validate(stdout=raw)
        self.assertEqual(actual["sites"]["site1"]["servers"]["srv1"]["df"], raw)
        filesystems = [r for r in results if ".filesystem." in r.check_id]
        self.assertEqual(
            [r.status for r in filesystems],
            [CheckStatus.PASS, CheckStatus.WARNING] * 2,
        )
        chrony = [r for r in results if ".chrony." in r.check_id]
        self.assertEqual(len(chrony), 8)
        self.assertTrue(all(r.status == CheckStatus.PASS for r in chrony))
        self.assertEqual(
            [call.kwargs["command"] for call in ssh.run.call_args_list].count(COMMAND), 2
        )

    def test_one_two_three_sites_and_multiple_servers_are_independent(self):
        for count in (1, 2, 3):
            with self.subTest(count=count):
                config = dynamic_config(count)
                for index in range(1, count + 1):
                    site = f"region-{index}"
                    outputs = config["servers"]["fixture_actual"]["sites"][site]["servers"]
                    outputs[f"srv{index}"]["df"] = (
                        mapper_line(40) + mapper_line(
                            (40, 70, 80)[index - 1], "/var", "/dev/mapper/vg-var"
                        )
                    )
                expected = config["servers"]["sites"]["region-1"]["servers"]
                expected.append({**expected[0], "id": "extra"})
                config["servers"]["fixture_actual"]["sites"]["region-1"]["servers"]["extra"] = {
                    "reachable": True,
                    "df": mapper_line(69),
                    "chrony": {"synchronized": True, "timezone": "UTC",
                               "source": "fixture-ntp", "offset": 0.1},
                }
                results = InfrastructureValidator().validate(
                    config["servers"]["fixture_actual"], config, self.context(config)
                )
                filesystems = [r for r in results if ".filesystem." in r.check_id]
                self.assertEqual(len(filesystems), count * 2 + 1)
                summaries = site_summaries(results, config)
                self.assertEqual(
                    [s.status for s in summaries],
                    [CheckStatus.PASS, CheckStatus.WARNING, CheckStatus.FAIL][:count],
                )
                self.assertEqual(
                    len([r for r in filesystems if r.target == "extra"]), 1
                )

    def test_policy_rejects_mount_selection_wrong_command_match_or_optional_discovery(self):
        for field, value in (
            ("path", "/"),
            ("command", "df"),
            ("match", "sda"),
            ("required", False),
        ):
            with self.subTest(field=field):
                config = load_fixture_config()
                config["servers"]["sites"]["site1"]["servers"][0]["filesystems"][0][field] = value
                report = validate_config(config, production_preflight=False)
                self.assertFalse(report.ok)
                self.assertTrue(any(f"filesystems[0].{field}" in line for line in report.lines()))

    def test_mapper_policy_comes_from_rules_and_cannot_be_overridden_by_env(self):
        env = valid_env()
        env["FILESYSTEM_COMMAND"] = "df"
        env["FILESYSTEM_MATCH"] = "sda"
        config = load_config_dir("deploy/docker/config", env=env)
        for site in config["servers"]["sites"].values():
            policy = site["servers"][0]["filesystems"][0]
            self.assertEqual(policy["command"], COMMAND)
            self.assertEqual(policy["match"], "mapper")
            self.assertNotIn("path", policy)


if __name__ == "__main__":
    unittest.main()
