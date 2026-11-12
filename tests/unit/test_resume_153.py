from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api import dependencies
from app.config.validation import validate_config
from app.database.repository import Repository
from app.domain import CheckResult, CheckStatus, NoteScope, ReviewDecision, ReviewNote, RunState
from app.evidence.manager import EvidenceManager
from app.evidence.redaction import sanitize
from app.orchestrator.lock import InvalidRunTransition
from app.reporting.snapshot import finalize_run
from app.web.main import create_app
from tests.fixtures.config_factory import load_fixture_config


class Resume153Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.url = "sqlite:///" + str(self.root / "fixture.sqlite")
        self.repo = Repository(self.url)
        self.addCleanup(lambda: self.repo.close())
        self.evidence = EvidenceManager(self.root / "evidence")
        self.config = load_fixture_config()
        self.config["manual_db_check"]["required"] = False
        self.repo.create_run(started_by="fixture", run_id="WR-RESUME-153")
        self.repo.claim_next_run("fixture")
        self.repo.add_result(
            CheckResult(
                "WR-RESUME-153",
                "rabbitmq",
                "rabbitmq.fixture",
                CheckStatus.FAIL,
                "Resource failed",
                site="site1",
                actual={"used": 100},
            )
        )
        self.repo.mark_review_ready("WR-RESUME-153", CheckStatus.FAIL)
        self.repo.save_note(
            ReviewNote(
                "WR-RESUME-153",
                NoteScope.GENERAL,
                "fixture",
                "Retained existing note",
            )
        )

    def finalize(self, **kwargs):
        return finalize_run(
            self.repo,
            self.evidence,
            self.config,
            "WR-RESUME-153",
            " Operator ",
            ReviewDecision.REJECT,
            rejection_reason=" password=sentinel retry ",
            **kwargs,
        )

    def test_retry_normalizes_original_identity_and_redacted_reason(self):
        with self.assertRaises(RuntimeError):
            self.finalize(fail_pdf=True)
        path = self.evidence.absolute(self.repo.get_run("WR-RESUME-153").final_snapshot_path)
        before = path.read_bytes()
        snapshot = self.finalize()
        self.assertEqual(path.read_bytes(), before)
        self.assertNotIn("sentinel", json.dumps(snapshot))
        self.assertEqual(self.repo.get_run("WR-RESUME-153").state, RunState.REJECTED)

    def test_schema_upgrade_preserves_all_existing_rows_and_artifacts(self):
        self.finalize()
        self.repo._execute("ALTER TABLE runs DROP COLUMN rejection_reason")
        tables = ["runs", "results", "evidence", "review_notes", "manual_db_reviews", "run_lock"]
        before = {
            table: [dict(row) for row in self.repo._execute("SELECT * FROM " + table)]
            for table in tables
        }
        artifacts = {p: p.read_bytes() for p in self.evidence.root.rglob("*") if p.is_file()}
        self.repo.close()
        self.repo = Repository(self.url)
        self.repo.init_schema()
        for table in tables:
            after = [dict(row) for row in self.repo._execute("SELECT * FROM " + table)]
            if table == "runs":
                for row in after:
                    self.assertIsNone(row.pop("rejection_reason"))
            self.assertEqual(after, before[table])
        self.assertEqual(artifacts, {p: p.read_bytes() for p in artifacts})

    def test_concurrent_finalization_creates_one_immutable_report(self):
        outcomes = []

        def finish():
            try:
                self.finalize()
                outcomes.append("success")
            except InvalidRunTransition:
                outcomes.append("already finalized")

        threads = [threading.Thread(target=finish) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
            self.assertFalse(thread.is_alive())
        self.assertCountEqual(outcomes, ["success", "already finalized"])
        self.assertEqual(len(list(self.evidence.root.rglob("*.pdf"))), 1)
        self.assertEqual(len(list(self.evidence.root.rglob("review_snapshot.json"))), 1)

    def test_cookie_headers_are_redacted_in_text_evidence(self):
        raw = "Cookie: session=cookie-sentinel; preference=private\nnext line"
        clean = sanitize(raw)
        self.assertNotIn("cookie-sentinel", clean)
        self.assertNotIn("private", clean)
        self.assertIn("next line", clean)

    def test_api_render_failure_reports_frozen_snapshot_for_retry(self):
        app = create_app()
        app.dependency_overrides[dependencies.get_repository] = lambda: self.repo
        app.dependency_overrides[dependencies.get_evidence_manager] = lambda: self.evidence
        app.dependency_overrides[dependencies.get_config] = lambda: self.config
        app.dependency_overrides[dependencies.get_mutation_guard] = lambda: None
        with TestClient(app) as client:
            with patch(
                "app.reporting.snapshot.render_pdf_under", side_effect=RuntimeError("PDF failed")
            ):
                response = client.post(
                    "/api/runs/WR-RESUME-153/finalize",
                    json={
                        "reviewer": "Operator",
                        "decision": "REJECT",
                        "rejection_reason": "Failure",
                    },
                )
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.headers["X-Review-Snapshot-Frozen"], "1")
            response = client.post(
                "/api/runs/WR-RESUME-153/finalize",
                json={
                    "reviewer": "Operator",
                    "decision": "REJECT",
                    "rejection_reason": "Failure",
                },
            )
            self.assertEqual(response.status_code, 200)

    def test_chrony_uncertainty_policy_rejects_nonfinite_limits(self):
        for value in (float("nan"), float("inf"), -float("inf")):
            with self.subTest(value=value):
                for site in self.config["servers"]["sites"].values():
                    for server in site["servers"]:
                        for check in server["chrony"]:
                            check["max_root_distance_seconds"] = value
                report = validate_config(self.config, production_preflight=False)
                self.assertTrue(
                    any("max_root_distance_seconds" in issue.path for issue in report.errors)
                )
