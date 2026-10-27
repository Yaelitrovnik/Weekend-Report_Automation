from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api import dependencies
from app.database.repository import Repository
from app.domain import (
    CheckStatus,
    ManualDBReview,
    ManualDBReviewResult,
    NoteScope,
    ReviewDecision,
    ReviewNote,
    RunState,
)
from app.evidence.manager import EvidenceManager
from app.orchestrator.run_context import RunContext
from app.orchestrator.runner import OrchestratorRunner
from app.reporting.final_pdf import _result_lines
from app.reporting.html import render_final_html
from app.reporting.snapshot import finalize_run
from app.review.finalization import FinalizationReadinessError
from app.review.manual_db import record_manual_db_review
from app.web.main import create_app
from tests.fixtures.dynamic_sites import dynamic_config


class DynamicSiteWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Repository("sqlite:///:memory:")
        self.addCleanup(self.repo.close)
        self.evidence = EvidenceManager(Path(self.tmp.name))

    def complete_review(self, config, run_id):
        for module in config["rules"]["review"]["required_module_notes"]:
            self.repo.save_note(
                ReviewNote(
                    run_id,
                    NoteScope.MODULE,
                    "reviewer",
                    "Module reviewed",
                    module=module,
                )
            )
        for dashboard in config["splunk_dashboards"]["dashboards"]:
            self.repo.save_note(
                ReviewNote(
                    run_id,
                    NoteScope.SPLUNK_DASHBOARD,
                    "reviewer",
                    "Dashboard reviewed",
                    dashboard_id=dashboard["id"],
                    reviewed=True,
                )
            )
        self.repo.save_note(
            ReviewNote(
                run_id,
                NoteScope.GENERAL,
                "reviewer",
                "All checks reviewed",
            )
        )
        manual = config["manual_db_check"]
        record_manual_db_review(
            self.repo,
            ManualDBReview(
                run_id=run_id,
                display_name=manual["display_name"],
                script_path=manual["script_path"],
                result=ManualDBReviewResult.PASS,
                comment="Manual check completed",
                reviewer="reviewer",
            ),
        )

    def run_config(self, config, run_id):
        self.repo.create_run(started_by="tester", run_id=run_id)
        self.repo.claim_next_run("worker")
        return OrchestratorRunner().run(RunContext(run_id, config, self.repo, self.evidence))

    def client(self, config):
        app = create_app()
        app.dependency_overrides[dependencies.get_repository] = lambda: self.repo
        app.dependency_overrides[dependencies.get_config] = lambda: config
        app.dependency_overrides[dependencies.get_evidence_manager] = lambda: self.evidence
        client = TestClient(app)
        self.addCleanup(client.close)
        return client

    def test_single_site_skips_parity_execution_and_evidence_entirely(self):
        config = dynamic_config(1)
        with patch("app.orchestrator.runner.SiteParityValidator") as parity:
            results = self.run_config(config, "WR-20261019-000001")
            parity.assert_not_called()
        self.assertTrue(results)
        self.assertTrue(all(r.status == CheckStatus.PASS for r in results))
        self.assertFalse(any(r.module == "site_parity" for r in results))
        self.assertFalse(
            any(e.module == "site_parity" for e in self.repo.list_evidence("WR-20261019-000001"))
        )

    def test_one_two_three_sites_render_and_approve_with_all_review_requirements(self):
        for count in (1, 2, 3):
            with self.subTest(count=count):
                config = dynamic_config(count)
                run_id = f"WR-20261019-{count:06d}"
                results = self.run_config(config, run_id)
                client = self.client(config)
                self.assertEqual(self.repo.get_run(run_id).state, RunState.REVIEW_READY)
                with self.assertRaises(FinalizationReadinessError):
                    finalize_run(
                        self.repo, self.evidence, config, run_id, "reviewer", ReviewDecision.APPROVE
                    )
                for module in ("portainer", "doctor", "rabbitmq", "infrastructure"):
                    response = client.get(f"/runs/{run_id}/{module}")
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertIn("Independent Site Status", response.text)
                    for index in range(1, count + 1):
                        self.assertIn(f"Region {index}", response.text)
                    self.assertNotIn(f"Region {count + 1}", response.text)
                    self.assertNotIn("Two-Site Comparison", response.text)
                    self.assertEqual(
                        "data-cross-site-parity" in response.text,
                        count > 1 and module in ("portainer", "doctor"),
                    )
                for unsupported in ("recording", "unknown"):
                    self.assertEqual(client.get(f"/runs/{run_id}/{unsupported}").status_code, 404)
                review = client.get(f"/runs/{run_id}/review")
                self.assertEqual(review.status_code, 200)
                if count == 1:
                    self.assertNotIn("Site Parity", review.text)
                self.complete_review(config, run_id)
                snapshot = finalize_run(
                    self.repo, self.evidence, config, run_id, "reviewer", ReviewDecision.APPROVE
                )
                self.assertEqual(self.repo.get_run(run_id).state, RunState.APPROVED)
                password = config["servers"]["ssh"]["password"]
                self.assertNotIn(password, json.dumps(snapshot))
                self.assertNotIn(password, render_final_html(snapshot))
                self.assertNotIn(password, review.text)
                infra_page = client.get(f"/runs/{run_id}/infrastructure")
                self.assertNotIn(password, infra_page.text)
                for artifact in self.evidence.root.rglob("*"):
                    if artifact.is_file():
                        self.assertNotIn(password.encode(), artifact.read_bytes())
                self.assertEqual(len(snapshot["site_summaries"]), count)
                self.assertEqual(len(snapshot["parity_summaries"]), 7 if count > 1 else 0)
                self.assertTrue(all(r.status == CheckStatus.PASS for r in results))
                pdf_path = self.repo.get_run(run_id).final_pdf_path
                self.assertTrue((self.evidence.root / pdf_path).read_bytes().startswith(b"%PDF"))
                html = render_final_html(snapshot)
                for index in range(1, count + 1):
                    self.assertIn(f"region-{index}", html)

    def test_ui_shows_real_parity_mismatch_when_all_site_health_passes(self):
        config = dynamic_config(3)
        config["portainer_expected"]["fixture_actual"]["sites"]["region-3"]["services"][0][
            "image"
        ] = "api:old"
        run_id = "WR-20261019-000001"
        results = self.run_config(config, run_id)
        self.assertTrue(all(r.status == CheckStatus.PASS for r in results if r.site))
        response = self.client(config).get(f"/runs/{run_id}/portainer")
        self.assertIn("Cross-Site Parity", response.text)
        self.assertIn("status-fail", response.text)
        self.assertIn("api:old", response.text)
        self.assertIn("region-3", response.text)

    def test_same_server_id_on_different_sites_has_separate_cards(self):
        config = dynamic_config(3)
        for index in range(1, 4):
            site = f"region-{index}"
            config["servers"]["sites"][site]["servers"][0]["id"] = "shared-name"
            outputs = config["servers"]["fixture_actual"]["sites"][site]["servers"]
            outputs["shared-name"] = outputs.pop(f"srv{index}")
        run_id = "WR-20261019-000001"
        self.run_config(config, run_id)
        response = self.client(config).get(f"/runs/{run_id}/infrastructure")
        self.assertEqual(response.text.count("<h2>shared-name</h2>"), 3)


    def test_multiple_mappers_survive_ui_evidence_and_finalization_per_site(self):
        config = dynamic_config(3)
        for index in range(1, 4):
            site = f"region-{index}"
            server = config["servers"]["fixture_actual"]["sites"][site]["servers"][f"srv{index}"]
            # No root mount; every site discovers two mapper filesystems.
            usage = (40, 70, 85)[index - 1]
            server["df"] = (
                f"/dev/mapper/site{index}-data 100G 40G 60G 40% /data\n"
                f"/dev/mapper/site{index}-var 100G 40G 60G {usage}% /var\n"
            )
        run_id = "WR-20261020-000001"
        results = self.run_config(config, run_id)
        filesystems = [r for r in results if r.check_id == "infrastructure.filesystem.utilization"]
        self.assertEqual(len(filesystems), 6)
        self.assertEqual(
            [r.status for r in filesystems],
            [CheckStatus.PASS, CheckStatus.PASS, CheckStatus.PASS,
             CheckStatus.WARNING, CheckStatus.PASS, CheckStatus.FAIL],
        )
        self.assertEqual(self.repo.get_run(run_id).automation_status, CheckStatus.FAIL)
        response = self.client(config).get(f"/runs/{run_id}/infrastructure")
        self.assertEqual(response.status_code, 200)
        for index in range(1, 4):
            self.assertIn(f"Region {index}", response.text)
            self.assertIn(f"<h2>srv{index}</h2>", response.text)
        for result in filesystems:
            self.assertIn(result.message, response.text)
            self.assertIn(f"status-{result.status.value.lower()}", response.text)
            normalized = next(
                e for e in self.repo.list_evidence(run_id)
                if e.result_id == result.id and e.evidence_type == "normalized_result"
            )
            saved = json.loads((self.evidence.root / normalized.path).read_text())
            self.assertEqual(saved["result"]["actual"], result.actual)
            self.assertEqual(saved["result"]["site"], result.site)
            self.assertEqual(saved["result"]["target"], result.target)
        raw = next(
            e for e in self.repo.list_evidence(run_id)
            if e.module == "infrastructure" and e.evidence_type == "raw_collector"
        )
        collected = json.loads((self.evidence.root / raw.path).read_text())
        self.assertEqual(collected, config["servers"]["fixture_actual"])
        self.complete_review(config, run_id)
        with self.assertRaises(FinalizationReadinessError):
            finalize_run(
                self.repo, self.evidence, config, run_id, "reviewer", ReviewDecision.APPROVE
            )
        manual = config["manual_db_check"]
        record_manual_db_review(self.repo, ManualDBReview(
            run_id=run_id, display_name=manual["display_name"],
            script_path=manual["script_path"], result=ManualDBReviewResult.FAIL,
            comment="Manual check failed; reject the report.", reviewer="reviewer",
        ))
        snapshot = finalize_run(
            self.repo, self.evidence, config, run_id, "reviewer", ReviewDecision.REJECT
        )
        self.assertEqual(self.repo.get_run(run_id).state, RunState.REJECTED)
        saved_filesystems = [
            r for r in snapshot["results"]
            if r["check_id"] == "infrastructure.filesystem.utilization"
        ]
        self.assertEqual(len(saved_filesystems), 6)
        self.assertEqual(
            [s["status"] for s in snapshot["site_summaries"]], ["PASS", "WARNING", "FAIL"]
        )
        html = render_final_html(snapshot)
        pdf_lines = "\n".join(_result_lines(saved_filesystems))
        for result in filesystems:
            self.assertIn(result.message, html)
            self.assertIn(result.message, pdf_lines)
            self.assertIn(f"site: {result.site}", pdf_lines)
            self.assertIn(f"target: {result.target}", pdf_lines)
        pdf_path = self.repo.get_run(run_id).final_pdf_path
        self.assertTrue((self.evidence.root / pdf_path).read_bytes().startswith(b"%PDF"))

    def test_missing_doctor_service_is_not_presented_as_healthy(self):
        config = dynamic_config(3)
        del config["doctor"]["doctor"]["fixture_actual"]["sites"]["region-3"]["services"][
            "service-01"
        ]
        run_id = "WR-20261019-000001"
        self.run_config(config, run_id)
        response = self.client(config).get(f"/runs/{run_id}/doctor")
        service = next(
            s for s in response.context["doctor_services"] if s["service"] == "service-01"
        )
        missing = next(s for s in service["site_rows"] if s["site"] == "region-3")
        self.assertEqual(missing["reason"], "Not captured")
        self.assertNotEqual(missing["status"], "PASS")
        self.assertIn("Cross-Site Parity", response.text)
