from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from app.config.loader import load_config_dir
from app.database.repository import Repository
from app.domain import CheckStatus
from app.evidence.manager import EvidenceManager
from app.orchestrator.run_context import RunContext
from app.validators.recording import RecordingValidator


class RecordingSafetyTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config_dir("tests/fixtures/config_valid")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ctx = RunContext(
            "WR-20260811-000000",
            self.config,
            Repository("sqlite:///:memory:"),
            EvidenceManager(Path(self.tmp.name)),
        )
        self.addCleanup(self.ctx.repository.close)

    def validate(self, site_actual: dict) -> list:
        return RecordingValidator().validate(
            {"sites": {"site1": site_actual}, "errors": []},
            self.config,
            self.ctx,
        )

    def fixture_site(self) -> dict:
        return copy.deepcopy(self.config["recording"]["fixture_actual"]["sites"]["site1"])

    def test_existing_device_workflow_passes_without_create_or_delete_identity(self):
        results = self.validate(self.fixture_site())
        self.assertEqual(
            [r.status for r in results if r.check_id == "recording.module_status"],
            [CheckStatus.PASS],
        )
        self.assertFalse(any("identity" in str(r.expected).lower() for r in results))

    def test_cleanup_failure_blocks_module(self):
        site = self.fixture_site()
        site["cleanup"] = {"success": True, "complete": False}
        results = self.validate(site)
        self.assertEqual(
            [r.status for r in results if r.check_id == "recording.cleanup"],
            [CheckStatus.FAIL],
        )
        self.assertEqual(
            [r.status for r in results if r.check_id == "recording.module_status"],
            [CheckStatus.FAIL],
        )

    def test_no_eligible_existing_device_is_error_until_policy_is_approved(self):
        site = self.fixture_site()
        site["device_selection"] = {"success": False, "reason": "no_eligible_device"}
        results = self.validate(site)
        self.assertEqual(
            [r.status for r in results if r.check_id == "recording.device_selection"],
            [CheckStatus.ERROR],
        )

    def test_recovery_required_blocks_module_without_replay(self):
        site = self.fixture_site()
        site["recovery_required"] = True
        results = self.validate(site)
        module_status = [r for r in results if r.check_id == "recording.module_status"][0]
        self.assertEqual(module_status.status, CheckStatus.ERROR)
        self.assertTrue(module_status.metadata["recovery_required"])


if __name__ == "__main__":
    unittest.main()
