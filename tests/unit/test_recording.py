from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from app.collectors.recording import RecordingCollector
from app.database.repository import Repository
from app.domain import CheckStatus, RunState
from app.evidence.manager import EvidenceManager
from app.orchestrator.run_context import RunContext
from app.orchestrator.runner import OrchestratorRunner
from app.validators.recording import OBSERVATION_POINTS, RecordingValidator
from tests.fixtures.config_factory import load_fixture_config


class RecordingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_fixture_config()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Repository("sqlite:///:memory:")
        self.addCleanup(self.repo.close)
        self.ctx = RunContext(
            "WR-20260811-000000",
            self.config,
            self.repo,
            EvidenceManager(Path(self.tmp.name)),
        )

    def fixture_actual(self) -> dict:
        return copy.deepcopy(self.config["recording"]["fixture_actual"])

    def validate(self, actual: dict) -> list:
        return RecordingValidator().validate(actual, self.config, self.ctx)

    def result(self, results: list, check_id: str):
        return [result for result in results if result.check_id == check_id][0]

    def test_fixture_workflow_captures_four_dynamic_baselines_and_restores_them(self):
        actual = self.fixture_actual()
        results = self.validate(actual)
        self.assertEqual(
            [
                self.result(results, f"recording.baseline.{point}").status
                for point in OBSERVATION_POINTS
            ],
            [CheckStatus.PASS] * 4,
        )
        for point in OBSERVATION_POINTS:
            baseline = actual["observations"]["baseline"][point]["count"]
            after_start = self.result(results, f"recording.after_start.{point}")
            after_stop = self.result(results, f"recording.after_stop.{point}")
            self.assertEqual(after_start.status, CheckStatus.PASS)
            self.assertEqual(after_start.expected["expected_count"], baseline + 1)
            self.assertEqual(after_stop.status, CheckStatus.PASS)
            self.assertEqual(after_stop.expected["expected_count"], baseline)
        self.assertEqual(
            self.result(results, "recording.module_status").status,
            CheckStatus.PASS,
        )

    def test_after_start_count_mismatch_is_functional_fail(self):
        actual = self.fixture_actual()
        actual["observations"]["after_start"]["site1_webapp"]["count"] = 99
        results = self.validate(actual)
        mismatch = self.result(results, "recording.after_start.site1_webapp")
        self.assertEqual(mismatch.status, CheckStatus.FAIL)
        self.assertFalse(mismatch.metadata.get("recovery_required", False))
        self.assertEqual(self.result(results, "recording.module_status").status, CheckStatus.FAIL)

    def test_collection_failure_is_error(self):
        actual = self.fixture_actual()
        actual["observations"]["baseline"]["site2_server"] = {"reliable": False}
        results = self.validate(actual)
        baseline = self.result(results, "recording.baseline.site2_server")
        self.assertEqual(baseline.status, CheckStatus.ERROR)
        self.assertEqual(baseline.metadata["error_code"], "RECORDING_COLLECTION_ERROR")

    def test_after_stop_mismatch_after_start_requires_recovery(self):
        actual = self.fixture_actual()
        actual["observations"]["after_stop"]["site2_webapp"]["count"] = 99
        results = self.validate(actual)
        after_stop = self.result(results, "recording.after_stop.site2_webapp")
        module_status = self.result(results, "recording.module_status")
        self.assertEqual(after_stop.status, CheckStatus.ERROR)
        self.assertTrue(after_stop.metadata["recovery_required"])
        self.assertEqual(after_stop.metadata["error_code"], "RECORDING_RECOVERY_REQUIRED")
        self.assertEqual(module_status.status, CheckStatus.ERROR)
        self.assertTrue(module_status.metadata["cleanup_required"])

    def test_stop_control_failure_after_start_requires_recovery(self):
        actual = self.fixture_actual()
        actual["stop_action"] = {"success": False}
        results = self.validate(actual)
        stop = self.result(results, "recording.stop_action")
        self.assertEqual(stop.status, CheckStatus.ERROR)
        self.assertTrue(stop.metadata["recovery_required"])

    def test_cleanup_failure_requires_recovery(self):
        actual = self.fixture_actual()
        actual["cleanup"] = {"success": True, "complete": False}
        results = self.validate(actual)
        cleanup = self.result(results, "recording.cleanup")
        self.assertEqual(cleanup.status, CheckStatus.ERROR)
        self.assertTrue(cleanup.metadata["recovery_required"])

    def test_live_recording_collector_remains_blocked(self):
        config = copy.deepcopy(self.config)
        config["recording"]["collection_mode"] = "live"
        actual = RecordingCollector().collect(
            RunContext(self.ctx.run_id, config, self.repo, self.ctx.evidence)
        )
        results = RecordingValidator().validate(actual, config, self.ctx)
        self.assertEqual(results[0].status, CheckStatus.ERROR)
        self.assertTrue(results[0].metadata["blocked_live_execution"])

    def test_orchestrator_moves_recovery_required_run_to_blocking_state(self):
        config = copy.deepcopy(self.config)
        for module, rule in config["rules"]["modules"].items():
            rule["enabled"] = module == "recording"
            rule["required"] = module == "recording"
        config["recording"]["fixture_actual"]["cleanup"] = {"success": True, "complete": False}
        run = self.repo.create_run(
            started_by="tester",
            run_id="WR-20260811-000010",
            config_version=config["_config_hash"],
        )
        self.repo.claim_next_run("worker")
        OrchestratorRunner().run(RunContext(run.run_id, config, self.repo, self.ctx.evidence))
        stored = self.repo.get_run(run.run_id)
        self.assertEqual(stored.state, RunState.RECOVERY_REQUIRED)
        self.assertEqual(stored.automation_status, CheckStatus.ERROR)

    def test_unknown_start_outcome_requires_recovery_and_is_not_replayed(self):
        actual = self.fixture_actual()
        actual["start_action"] = {
            "success": False,
            "reliable": False,
            "error": "request timed out",
        }

        results = self.validate(actual)

        start = self.result(results, "recording.start_action")
        module_status = self.result(results, "recording.module_status")

        self.assertEqual(start.status, CheckStatus.ERROR)
        self.assertTrue(start.metadata["recovery_required"])
        self.assertEqual(module_status.status, CheckStatus.ERROR)
        self.assertTrue(module_status.metadata["recovery_required"])


if __name__ == "__main__":
    unittest.main()
