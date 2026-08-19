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


if __name__ == "__main__":
    unittest.main()
