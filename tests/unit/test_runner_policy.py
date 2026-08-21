from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from app.config.loader import load_config_dir
from app.database.repository import Repository
from app.domain import CheckStatus
from app.evidence.manager import EvidenceManager
from app.orchestrator import runner
from app.orchestrator.run_context import RunContext
from app.orchestrator.runner import OrchestratorRunner


class FailingCollector:
    def collect(self, context: RunContext) -> dict[str, Any]:
        raise RuntimeError("fixture collector unavailable")


class RunnerPolicyTests(unittest.TestCase):
    def test_if_unavailable_status_is_applied_to_module_error(self):
        config = copy.deepcopy(load_config_dir("tests/fixtures/config_valid"))
        for name, rule in config["rules"]["modules"].items():
            rule["enabled"] = name == "portainer"
            rule["required"] = name == "portainer"
        config["rules"]["modules"]["portainer"]["if_unavailable_status"] = "WARNING"
        config["rules"]["parity"] = []
        with tempfile.TemporaryDirectory() as tmp:
            repo = Repository("sqlite:///:memory:")
            self.addCleanup(repo.close)
            evidence = EvidenceManager(Path(tmp) / "evidence")
            run = repo.create_run(started_by="tester", run_id="WR-20260811-000000")
            repo.claim_next_run("worker")
            with patch.dict(runner.COLLECTORS, {"portainer": FailingCollector}):
                OrchestratorRunner().run(RunContext(run.run_id, config, repo, evidence))
            result = repo.list_results(run.run_id, "portainer")[0]
            self.assertEqual(result.status, CheckStatus.WARNING)
            self.assertEqual(result.metadata["configured_if_unavailable_status"], "WARNING")
            self.assertEqual(repo.get_run(run.run_id).automation_status, CheckStatus.WARNING)

    def test_runner_logs_collector_and_validator_duration_for_success(self):
        config = copy.deepcopy(load_config_dir("tests/fixtures/config_valid"))
        for name, rule in config["rules"]["modules"].items():
            rule["enabled"] = name == "portainer"
            rule["required"] = name == "portainer"
        config["rules"]["parity"] = []
        with tempfile.TemporaryDirectory() as tmp:
            repo = Repository("sqlite:///:memory:")
            self.addCleanup(repo.close)
            evidence = EvidenceManager(Path(tmp) / "evidence")
            run = repo.create_run(started_by="tester", run_id="WR-20260811-000001")
            repo.claim_next_run("worker")
            with self.assertLogs("app.orchestrator.runner", level="INFO") as captured:
                OrchestratorRunner().run(RunContext(run.run_id, config, repo, evidence))
        events = {getattr(record, "event", None) for record in captured.records}
        self.assertIn("collector_finish", events)
        self.assertIn("validator_finish", events)
        collector_record = next(
            record
            for record in captured.records
            if getattr(record, "event", None) == "collector_finish"
        )
        self.assertEqual(collector_record.module_name, "portainer")
        self.assertEqual(collector_record.run_id, run.run_id)
        self.assertIsInstance(collector_record.duration_ms, int)
        self.assertEqual(collector_record.outcome, "success")

    def test_runner_logs_module_error_with_duration_on_failure(self):
        config = copy.deepcopy(load_config_dir("tests/fixtures/config_valid"))
        for name, rule in config["rules"]["modules"].items():
            rule["enabled"] = name == "portainer"
            rule["required"] = name == "portainer"
        config["rules"]["modules"]["portainer"]["if_unavailable_status"] = "WARNING"
        config["rules"]["parity"] = []
        with tempfile.TemporaryDirectory() as tmp:
            repo = Repository("sqlite:///:memory:")
            self.addCleanup(repo.close)
            evidence = EvidenceManager(Path(tmp) / "evidence")
            run = repo.create_run(started_by="tester", run_id="WR-20260811-000002")
            repo.claim_next_run("worker")
            with patch.dict(runner.COLLECTORS, {"portainer": FailingCollector}):
                with self.assertLogs("app.orchestrator.runner", level="INFO") as captured:
                    OrchestratorRunner().run(RunContext(run.run_id, config, repo, evidence))
        error_record = next(
            record
            for record in captured.records
            if getattr(record, "event", None) == "module_error"
        )
        self.assertEqual(error_record.module_name, "portainer")
        self.assertEqual(error_record.outcome, "error")
        self.assertIsInstance(error_record.duration_ms, int)
        self.assertEqual(error_record.exception, "RuntimeError")

if __name__ == "__main__":
    unittest.main()
