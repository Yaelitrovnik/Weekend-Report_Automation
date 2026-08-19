from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from app.config.loader import load_config_dir
from app.database.repository import Repository
from app.domain import NoteScope, ReviewDecision, ReviewNote, RunState
from app.evidence.manager import EvidenceManager
from app.orchestrator.lock import DuplicateActiveRun
from app.orchestrator.run_context import RunContext
from app.orchestrator.runner import OrchestratorRunner
from app.reporting.html import render_final_html
from app.reporting.snapshot import finalize_run
from app.review.finalization import FinalizationReadinessError


class RunWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = Repository("sqlite:///:memory:")
        self.addCleanup(self.repo.close)
        self.evidence = EvidenceManager(self.root / "evidence")
        self.config = load_config_dir("tests/fixtures/config_valid")

    def approval_config_with_result_notes_for_failures(self) -> dict:
        config = copy.deepcopy(self.config)
        config["rules"]["review"]["approval_status_policy"]["FAIL"] = "REQUIRE_NOTE"
        return config

    def save_required_approval_notes(self, run_id: str) -> None:
        self.repo.save_note(
            ReviewNote(run_id, NoteScope.MODULE, "reviewer", "module note", module="portainer")
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
        with self.assertRaises(FinalizationReadinessError):
            finalize_run(
                self.repo,
                self.evidence,
                self.config,
                run.run_id,
                "reviewer",
                ReviewDecision.APPROVE,
            )
        approval_config = self.approval_config_with_result_notes_for_failures()
        snapshot = finalize_run(
            self.repo,
            self.evidence,
            approval_config,
            run.run_id,
            "reviewer",
            ReviewDecision.APPROVE,
        )
        self.assertEqual(self.repo.get_run(run.run_id).state, RunState.APPROVED)
        self.assertTrue(snapshot["site_summaries"])
        self.assertTrue(snapshot["module_summaries"])
        self.assertTrue(snapshot["parity_summaries"])
        self.assertEqual(snapshot["run"]["build_id"], "LOCAL-FOLDER")
        self.assertEqual(snapshot["configuration"]["hash"], self.config["_config_hash"])
        self.assertEqual(
            snapshot["overall_status"], self.repo.get_run(run.run_id).automation_status.value
        )
        note_text = [n["note"] for n in snapshot["notes"]]
        self.assertIn("module note", note_text)
        self.assertIn("result note", note_text)
        for dashboard in self.config["splunk_dashboards"]["dashboards"]:
            self.assertIn(f"note for {dashboard['id']}", note_text)
        html = render_final_html(snapshot)
        self.assertIn("module note", html)
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
        finalize_run(
            self.repo, self.evidence, self.config, run.run_id, "reviewer", ReviewDecision.REJECT
        )
        self.assertEqual(self.repo.get_run(run.run_id).state, RunState.REJECTED)

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
            finalize_run(
                self.repo,
                self.evidence,
                self.approval_config_with_result_notes_for_failures(),
                run.run_id,
                "reviewer",
                ReviewDecision.APPROVE,
                fail_pdf=True,
            )
        self.assertTrue(self.repo.get_run(run.run_id).final_snapshot_path)
        self.assertEqual(self.repo.get_run(run.run_id).state, RunState.REVIEW_READY)


if __name__ == "__main__":
    unittest.main()
