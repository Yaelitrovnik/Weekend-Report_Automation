from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from app.database.repository import Repository
from app.domain import CheckStatus
from app.evidence.manager import EvidenceManager
from app.orchestrator.run_context import RunContext
from app.validators.recording import RecordingValidator
from tests.fixtures.config_factory import load_fixture_config


class RecordingSafetyTests(unittest.TestCase):
    def setUp(self):
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
        return RecordingValidator().validate(
            actual,
            self.config,
            self.ctx,
        )

    def result(self, results: list, check_id: str):
        return [result for result in results if result.check_id == check_id][0]

    def test_selected_device_must_be_confirmed_not_recording(self):
        actual = self.fixture_actual()
        actual["selected_device"].pop("recording", None)

        results = self.validate(actual)

        result = self.result(
            results,
            "recording.device_selection",
        )

        self.assertEqual(result.status, CheckStatus.ERROR)

    def test_start_unknown_requires_recovery(self):
        actual = self.fixture_actual()
        actual["start_action"] = {
            "success": False,
            "reliable": False,
            "error": "timeout",
        }

        results = self.validate(actual)

        start = self.result(
            results,
            "recording.start_action",
        )

        self.assertEqual(start.status, CheckStatus.ERROR)
        self.assertTrue(start.metadata["recovery_required"])

    def test_known_start_failure_does_not_require_recovery(self):
        actual = self.fixture_actual()
        actual["start_action"] = {
            "success": False,
            "reliable": True,
        }

        results = self.validate(actual)

        start = self.result(
            results,
            "recording.start_action",
        )

        self.assertEqual(start.status, CheckStatus.FAIL)
        self.assertFalse(start.metadata.get("recovery_required", False))

    def test_stop_failure_after_start_requires_recovery(self):
        actual = self.fixture_actual()
        actual["stop_action"] = {
            "success": False,
            "reliable": True,
        }

        results = self.validate(actual)

        stop = self.result(
            results,
            "recording.stop_action",
        )

        self.assertEqual(stop.status, CheckStatus.ERROR)
        self.assertTrue(stop.metadata["recovery_required"])

    def test_cleanup_requires_proven_stopped_state(self):
        actual = self.fixture_actual()
        actual["cleanup"] = {
            "success": True,
            "complete": True,
        }

        results = self.validate(actual)

        cleanup = self.result(
            results,
            "recording.cleanup",
        )

        self.assertEqual(cleanup.status, CheckStatus.ERROR)
        self.assertTrue(cleanup.metadata["recovery_required"])


if __name__ == "__main__":
    unittest.main()
