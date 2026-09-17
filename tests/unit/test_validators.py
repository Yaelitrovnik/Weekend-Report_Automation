from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from app.collectors.infrastructure import normalize_chrony
from app.database.repository import Repository
from app.domain import CheckStatus
from app.evidence.manager import EvidenceManager
from app.orchestrator.run_context import RunContext
from app.validators.infrastructure import InfrastructureValidator
from app.validators.portainer import PortainerValidator
from tests.fixtures.config_factory import load_fixture_config


class ValidatorTests(unittest.TestCase):
    def context(self, config):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)

        repo = Repository("sqlite:///:memory:")
        self.addCleanup(repo.close)

        return RunContext(
            "WR-20260811-000000",
            config,
            repo,
            EvidenceManager(Path(tmp.name)),
        )

    def test_portainer_parity_does_not_mask_failed_health(self):
        config = load_fixture_config()
        actual = copy.deepcopy(config["portainer_expected"]["fixture_actual"]["sites"])
        actual["site1"]["services"][0]["running_replicas"] = 2
        actual["site2"]["services"][0]["running_replicas"] = 2
        results = PortainerValidator().validate({"sites": actual}, config, self.context(config))
        replica_statuses = [
            r.status for r in results if r.check_id == "portainer.service.running_replicas"
        ]
        self.assertEqual(replica_statuses, [CheckStatus.FAIL, CheckStatus.FAIL])

    def test_infrastructure_filesystem_threshold_and_chrony_unsynced(self):
        config = load_fixture_config()
        actual = copy.deepcopy(config["servers"]["fixture_actual"])
        actual["sites"]["site1"]["servers"]["srv1"]["df"] = (
            "Filesystem Size Used Avail Use% Mounted on\n/dev/sda1 100G 75G 25G 75% /"
        )
        actual["sites"]["site2"]["servers"]["srv2"]["chrony"]["synchronized"] = False
        results = InfrastructureValidator().validate(actual, config, self.context(config))
        self.assertIn(
            CheckStatus.WARNING,
            [
                r.status
                for r in results
                if r.check_id == "infrastructure.filesystem.utilization" and r.site == "site1"
            ],
        )
        self.assertIn(
            CheckStatus.FAIL,
            [
                r.status
                for r in results
                if r.check_id == "infrastructure.chrony.synchronized" and r.site == "site2"
            ],
        )

    def test_infrastructure_malformed_df_is_collection_error(self):
        config = load_fixture_config()
        actual = copy.deepcopy(config["servers"]["fixture_actual"])
        actual["sites"]["site1"]["servers"]["srv1"]["df"] = "not df output"
        results = InfrastructureValidator().validate(actual, config, self.context(config))
        utilization = [
            r
            for r in results
            if r.check_id == "infrastructure.filesystem.utilization" and r.site == "site1"
        ][0]
        self.assertEqual(utilization.status, CheckStatus.ERROR)
        self.assertIn("could not be parsed", utilization.message)

    def test_infrastructure_unreachable_server_is_error(self):
        config = load_fixture_config()
        actual = copy.deepcopy(config["servers"]["fixture_actual"])
        server = actual["sites"]["site1"]["servers"]["srv1"]
        server["reachable"] = False
        server["df_error"] = "SSH connection failed"
        results = InfrastructureValidator().validate(actual, config, self.context(config))
        reachability = [
            r
            for r in results
            if r.check_id == "infrastructure.ssh.reachable" and r.site == "site1"
        ][0]
        self.assertEqual(reachability.status, CheckStatus.ERROR)
        self.assertEqual(reachability.actual["reachable"], False)
        self.assertIn("SSH connection failed", reachability.actual["error"])
        self.assertIn("unreachable", reachability.message)

    def test_chrony_parser_normalizes_tracking_and_sources(self):
        tracking = (
            "Reference ID    : 203.0.113.1 (fixture)\n"
            "System time     : 0.00123 seconds slow of NTP time\n"
            "Leap status     : Normal\n"
        )
        sources = "^* fixture-ntp 1 6 377 12 +10us[ +20us] +/- 10ms\n"
        self.assertEqual(
            normalize_chrony(tracking, sources),
            {
                "synchronized": True,
                "source": "fixture-ntp",
                "offset": 0.00123,
                "raw": {
                    "tracking": {
                        "offset": 0.00123,
                        "leap_status": "Normal",
                        "synchronized": True,
                    },
                    "sources": {"source": "fixture-ntp", "selected": True},
                },
            },
        )


if __name__ == "__main__":
    unittest.main()
