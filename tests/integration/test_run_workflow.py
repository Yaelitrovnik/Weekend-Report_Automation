from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from app.database.repository import Repository
from app.domain import (
    ManualDBReview,
    ManualDBReviewResult,
    NoteScope,
    ReviewDecision,
    ReviewNote,
    RunState,
)
from app.evidence.manager import EvidenceManager
from app.orchestrator.lock import DuplicateActiveRun
from app.orchestrator.run_context import RunContext
from app.orchestrator.runner import OrchestratorRunner
from app.reporting.html import render_final_html
from app.reporting.snapshot import finalize_run
from app.review.manual_db import record_manual_db_review
from tests.fixtures.config_factory import load_fixture_config


class RunWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = Repository("sqlite:///:memory:")
        self.addCleanup(self.repo.close)
        self.evidence = EvidenceManager(self.root / "evidence")
        self.config = load_fixture_config()

    def approval_config_with_result_notes_for_failures(self) -> dict:
        config = copy.deepcopy(self.config)
        config["rules"]["review"]["approval_status_policy"]["FAIL"] = "REQUIRE_NOTE"
        return config

    def save_manual_db_result(
        self,
        run_id: str,
        result: ManualDBReviewResult,
    ) -> None:
        record_manual_db_review(
            self.repo,
            ManualDBReview(
                run_id=run_id,
                display_name="Database Synchronization Check",
                script_path=(
                    "C:\\Scripts\\DatabaseSync\\"
                    "database_sync_check.ps1"
                ),
                result=result,
                comment=(
                    "Database synchronization manual check "
                    f"recorded as {result.value}."
                ),
                reviewer="reviewer",
            ),
        )

    def save_required_approval_notes(self, run_id: str) -> None:
        review = self.config["rules"]["review"]
        for module in review["required_module_notes"]:
            self.repo.save_note(
                ReviewNote(
                    run_id,
                    NoteScope.MODULE,
                    "reviewer",
                    f"module note for {module}",
                    module=module,
                )
            )
        for result in self.repo.list_results(run_id):
            if result.status.value != "PASS":
                self.repo.save_note(
                    ReviewNote(
                        run_id,
                        NoteScope.RESULT,
                        "reviewer",
                        f"manual review acknowledged for {result.check_id}",
                        result_id=result.id,
                    )
                )
        for dashboard in self.config["splunk_dashboards"]["dashboards"]:
            self.repo.save_note(
                ReviewNote(
                    run_id,
                    NoteScope.SPLUNK_DASHBOARD,
                    "reviewer",
                    f"note for {dashboard['id']}",
                    dashboard_id=dashboard["id"],
                    reviewed=True,
                )
            )
        if (
            review.get("general_notes_enabled")
            and review.get("general_note_required")
        ):
            self.repo.save_note(
                ReviewNote(
                    run_id,
                    NoteScope.GENERAL,
                    "reviewer",
                    "general approval note",
                )
            )

    def test_duplicate_run_rejected_and_worker_claim_single(self):
        self.repo.create_run(started_by="alice", run_id="WR-20260811-000000")
        with self.assertRaises(DuplicateActiveRun):
            self.repo.create_run(started_by="alice", run_id="WR-20260811-000001")
        claimed = self.repo.claim_next_run("worker-a")
        self.assertIsNotNone(claimed)
        self.assertIsNone(self.repo.claim_next_run("worker-b"))

    def test_fake_run_to_review_ready_and_notes_finalize_pdf(self):
        run = self.repo.create_run(
            started_by="alice",
            run_id="WR-20260811-000000",
            config_version=self.config["_config_hash"],
        )
        claimed = self.repo.claim_next_run("worker-a")
        self.assertEqual(claimed.run_id, run.run_id)
        OrchestratorRunner().run(RunContext(run.run_id, self.config, self.repo, self.evidence))
        self.assertEqual(self.repo.get_run(run.run_id).state, RunState.REVIEW_READY)
        evidence_records = self.repo.list_evidence(run.run_id)
        self.assertTrue(evidence_records)
        self.assertTrue(any(item.evidence_type == "raw_collector" for item in evidence_records))
        self.assertTrue(any(item.evidence_type == "normalized_result" for item in evidence_records))
        results = self.repo.list_results(run.run_id)
        result = results[0]
        self.assertTrue(result.evidence)
        self.repo.save_note(
            ReviewNote(
                run.run_id, NoteScope.MODULE, "reviewer", "module note", module=result.module
            )
        )
        self.repo.save_note(
            ReviewNote(run.run_id, NoteScope.RESULT, "reviewer", "result note", result_id=result.id)
        )
        self.save_required_approval_notes(run.run_id)
        self.save_manual_db_result(
            run.run_id,
            ManualDBReviewResult.PASS,
        )
        saved_manual_db = self.repo.get_manual_db_review(
            run.run_id
        )
        self.assertIsNotNone(saved_manual_db)
        assert saved_manual_db is not None
        snapshot = finalize_run(
            self.repo,
            self.evidence,
            self.config,
            run.run_id,
            "reviewer",
            ReviewDecision.APPROVE,
        )
        manual_db_snapshot = snapshot[
            "manual_db_review"
        ]
        self.assertIsNotNone(manual_db_snapshot)
        self.assertEqual(
            manual_db_snapshot["display_name"],
            saved_manual_db.display_name,
        )
        self.assertEqual(
            manual_db_snapshot["script_path"],
            saved_manual_db.script_path,
        )
        self.assertEqual(
            manual_db_snapshot["execution_method"],
            "Performed locally by reviewer",
        )
        self.assertEqual(
            manual_db_snapshot["result"],
            "PASS",
        )
        self.assertEqual(
            manual_db_snapshot["comment"],
            saved_manual_db.comment,
        )
        self.assertNotIn("reviewer", manual_db_snapshot)
        self.assertEqual(
            manual_db_snapshot["reviewed_at"],
            saved_manual_db.reviewed_at,
        )
        self.assertEqual(
            manual_db_snapshot["final_decision"],
            "APPROVE",
        )
        self.assertEqual(
            snapshot["snapshot_version"],
            2,
        )
        self.assertEqual(snapshot["review"]["reviewer"], "reviewer")
        final_run = self.repo.get_run(
            run.run_id
        )
        self.assertIsNotNone(
            final_run.final_snapshot_path
        )
        assert final_run.final_snapshot_path is not None
        frozen_snapshot_path = (
            self.evidence.root
            / final_run.final_snapshot_path
        )
        frozen_snapshot = json.loads(
            frozen_snapshot_path.read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            frozen_snapshot["manual_db_review"],
            snapshot["manual_db_review"],
        )
        self.assertEqual(
            frozen_snapshot["manual_db_review"][
                "final_decision"
            ],
            "APPROVE",
        )
        self.assertIsNotNone(final_run.final_pdf_path)
        assert final_run.final_pdf_path is not None
        pdf_path = (
            self.evidence.root
            / final_run.final_pdf_path
        )
        pdf_data = pdf_path.read_bytes()
        self.assertIn(
            b"Manual Database Synchronization Check",
            pdf_data,
        )
        self.assertIn(
            b"Result: PASS",
            pdf_data,
        )
        self.assertIn(
            b"Database synchronization",
            pdf_data,
        )
        self.assertEqual(
            self.repo.get_run(run.run_id).state,
            RunState.APPROVED,
        )
        self.assertTrue(snapshot["site_summaries"])
        self.assertTrue(snapshot["module_summaries"])
        self.assertTrue(snapshot["parity_summaries"])
        self.assertEqual(snapshot["run"]["build_id"], "LOCAL-FOLDER")
        self.assertEqual(snapshot["configuration"]["hash"], self.config["_config_hash"])
        self.assertEqual(
            snapshot["overall_status"], self.repo.get_run(run.run_id).automation_status.value
        )
        note_text = [n["note"] for n in snapshot["notes"]]
        expected_module_notes = [
            f"module note for {module}"
            for module in self.config["rules"]["review"]["required_module_notes"]
        ]
        for expected_note in expected_module_notes:
            self.assertIn(expected_note, note_text)
        self.assertIn("result note", note_text)
        for dashboard in self.config["splunk_dashboards"]["dashboards"]:
            self.assertIn(
                f"note for {dashboard['id']}",
                note_text,
            )
        html = render_final_html(snapshot)
        for expected_note in expected_module_notes:
            self.assertIn(expected_note, html)
        self.assertTrue(
            (self.evidence.root / self.repo.get_run(run.run_id).final_pdf_path).exists()
        )

    def test_reject_generates_pdf(self):
        run = self.repo.create_run(
            started_by="alice",
            run_id="WR-20260811-000000",
            config_version=self.config["_config_hash"],
        )
        self.repo.claim_next_run("worker-a")
        OrchestratorRunner().run(RunContext(run.run_id, self.config, self.repo, self.evidence))
        self.save_manual_db_result(
            run.run_id,
            ManualDBReviewResult.FAIL,
        )
        snapshot = finalize_run(
            self.repo,
            self.evidence,
            self.config,
            run.run_id,
            "reviewer",
            ReviewDecision.REJECT,
        )
        self.assertEqual(self.repo.get_run(run.run_id).state, RunState.REJECTED)
        self.assertEqual(
            snapshot["manual_db_review"]["result"],
            "FAIL",
        )
        self.assertEqual(
            snapshot["manual_db_review"][
                "final_decision"
            ],
            "REJECT",
        )

    def test_pdf_failure_preserves_snapshot(self):
        run = self.repo.create_run(
            started_by="alice",
            run_id="WR-20260811-000000",
            config_version=self.config["_config_hash"],
        )
        self.repo.claim_next_run("worker-a")
        OrchestratorRunner().run(RunContext(run.run_id, self.config, self.repo, self.evidence))
        self.save_required_approval_notes(run.run_id)
        with self.assertRaises(RuntimeError):
            self.save_manual_db_result(
                run.run_id,
                ManualDBReviewResult.FAIL,
            )
            finalize_run(
                self.repo,
                self.evidence,
                self.approval_config_with_result_notes_for_failures(),
                run.run_id,
                "reviewer",
                ReviewDecision.REJECT,
                fail_pdf=True,
            )
        self.assertTrue(self.repo.get_run(run.run_id).final_snapshot_path)
        self.assertEqual(self.repo.get_run(run.run_id).state, RunState.REVIEW_READY)


if __name__ == "__main__":
    unittest.main()
