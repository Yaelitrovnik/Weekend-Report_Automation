from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from app.collectors.database import DatabaseCollector
from app.collectors.infrastructure import normalize_chrony
from app.config.loader import load_config_dir
from app.database.repository import Repository
from app.domain import CheckStatus
from app.evidence.manager import EvidenceManager
from app.orchestrator.run_context import RunContext
from app.validators.database import DatabaseValidator
from app.validators.infrastructure import InfrastructureValidator
from app.validators.portainer import PortainerValidator
from app.validators.rabbitmq import RabbitMQValidator


class ValidatorTests(unittest.TestCase):
    def context(self, config):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return RunContext(
            "WR-20260811-000000",
            config,
            Repository("sqlite:///:memory:"),
            EvidenceManager(Path(tmp.name)),
        )

    def test_portainer_parity_does_not_mask_failed_health(self):
        config = load_config_dir("tests/fixtures/config_valid")
        actual = copy.deepcopy(config["portainer_expected"]["fixture_actual"]["sites"])
        actual["site1"]["services"][0]["running_replicas"] = 2
        actual["site2"]["services"][0]["running_replicas"] = 2
        results = PortainerValidator().validate({"sites": actual}, config, self.context(config))
        replica_statuses = [
            r.status for r in results if r.check_id == "portainer.service.running_replicas"
        ]
        self.assertEqual(replica_statuses, [CheckStatus.FAIL, CheckStatus.FAIL])

    def test_rabbitmq_threshold_boundaries_and_missing_queue(self):
        config = load_config_dir("tests/fixtures/config_valid")
        ctx = self.context(config)
        actual = copy.deepcopy(config["rabbitmq_expected"]["fixture_actual"]["sites"])
        actual["site1"]["queues"][0]["messages"] = 10
        actual["site2"]["queues"] = []
        results = RabbitMQValidator().validate({"sites": actual}, config, ctx)
        self.assertIn(
            CheckStatus.WARNING,
            [r.status for r in results if r.check_id == "rabbitmq.queue.backlog"],
        )
        self.assertIn(
            CheckStatus.FAIL, [r.status for r in results if r.check_id == "rabbitmq.queue.exists"]
        )

    def test_rabbitmq_common_topology_optional_queue_can_be_absent(self):
        config = load_config_dir("tests/fixtures/config_valid")
        config["rabbitmq_expected"]["topology"]["queues"]["optional.audit"] = {
            "vhost": "/",
            "name": "optional.audit",
            "required": False,
            "warning_messages": 10,
            "critical_messages": 20,
        }
        actual = copy.deepcopy(config["rabbitmq_expected"]["fixture_actual"]["sites"])
        results = RabbitMQValidator().validate({"sites": actual}, config, self.context(config))
        optional = [
            r
            for r in results
            if r.check_id == "rabbitmq.queue.exists" and r.target == "optional.audit"
        ]
        self.assertEqual([r.status for r in optional], [CheckStatus.SKIPPED] * 2)

    def test_infrastructure_missing_mount_and_chrony_unsynced(self):
        config = load_config_dir("tests/fixtures/config_valid")
        actual = copy.deepcopy(config["servers"]["fixture_actual"])
        actual["sites"]["site1"]["servers"]["srv1"]["df"] = (
            "Filesystem Size Used Avail Use% Mounted on\n/dev/sdb1 10G 1G 9G 10% /data"
        )
        actual["sites"]["site2"]["servers"]["srv2"]["chrony"]["synchronized"] = False
        results = InfrastructureValidator().validate(actual, config, self.context(config))
        self.assertIn(CheckStatus.FAIL, [r.status for r in results])

    def test_infrastructure_nfs_source_and_usability_are_validated(self):
        config = load_config_dir("tests/fixtures/config_valid")
        actual = copy.deepcopy(config["servers"]["fixture_actual"])
        actual["sites"]["site1"]["servers"]["srv1"]["nfs_mounts"][0]["source"] = "wrong:/export"
        actual["sites"]["site2"]["servers"]["srv2"]["nfs_mounts"][0]["usable"] = False
        results = InfrastructureValidator().validate(actual, config, self.context(config))
        source = [r for r in results if r.check_id == "infrastructure.nfs.source"]
        usable = [r for r in results if r.check_id == "infrastructure.nfs.usable"]
        self.assertIn(CheckStatus.FAIL, [r.status for r in source])
        self.assertIn(CheckStatus.FAIL, [r.status for r in usable])

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

    def test_database_structured_sync_contract(self):
        config = load_config_dir("tests/fixtures/config_valid")
        actual = copy.deepcopy(config["database"]["fixture_actual"])
        actual["sites"]["site1"]["replication_after_delete"] = False
        results = DatabaseValidator().validate(actual, config, self.context(config))
        self.assertIn(CheckStatus.FAIL, [r.status for r in results])
        self.assertIn(
            CheckStatus.FAIL,
            [
                r.status
                for r in results
                if r.check_id == "database.replication_after_delete"
            ],
        )

    def test_database_live_adapter_is_blocked_until_existing_function_is_supplied(self):
        config = load_config_dir("tests/fixtures/config_valid")
        config["database"] = {
            "collection_mode": "live",
            "adapter": "existing_sync_function",
            "function_reference": "approved.function.placeholder",
        }
        actual = DatabaseCollector().collect(self.context(config))
        self.assertEqual(actual["errors"][0]["code"], "DATABASE_SYNC_FUNCTION_NOT_PROVIDED")


if __name__ == "__main__":
    unittest.main()
