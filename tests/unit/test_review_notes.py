from __future__ import annotations

import copy
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api import dependencies
from app.config.loader import load_config_dir
from app.database.repository import Repository
from app.domain import CheckResult, CheckStatus, RunState
from app.evidence.manager import EvidenceManager
from app.web.main import create_app


class ReviewNoteApiTests(unittest.TestCase):
    def setUp(self):
        self.env_patch = patch.dict(os.environ, {"WEEKEND_REPORT_AUTH_MODE": "development"})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Repository("sqlite:///:memory:")
        self.addCleanup(self.repo.close)
        self.config = load_config_dir("tests/fixtures/config_valid")
        self.evidence = EvidenceManager(Path(self.tmp.name) / "evidence")
        app = create_app()
        app.dependency_overrides[dependencies.get_repository] = lambda: self.repo
        app.dependency_overrides[dependencies.get_config] = lambda: self.config
        app.dependency_overrides[dependencies.get_evidence_manager] = lambda: self.evidence
        self.client = TestClient(app)

    def make_review_ready_run(self, run_id: str = "WR-20260811-000000") -> str:
        self.repo.create_run(started_by="tester", run_id=run_id)
        self.repo.claim_next_run("worker")
        for module, rule in self.config["rules"]["modules"].items():
            if module == "splunk" or not rule.get("required"):
                continue
            self.repo.add_result(
                CheckResult(run_id, module, f"{module}.ok", CheckStatus.PASS, "ok", site="site1")
            )
        self.repo.mark_review_ready(run_id, CheckStatus.PASS)
        return run_id

    def save_required_dashboard_notes(self, run_id: str) -> None:
        for dashboard in self.config["splunk_dashboards"]["dashboards"]:
            if dashboard["id"] == "system_health":
                continue
            self.client.put(
                f"/api/runs/{run_id}/notes/splunk/{dashboard['id']}",
                json={"note": f"reviewed {dashboard['id']}"},
                headers={"X-Reviewer": "alice"},
            )

    def test_notes_are_blocked_before_review_ready(self):
        self.repo.create_run(started_by="tester", run_id="WR-20260811-000000")
        response = self.client.put(
            "/api/runs/WR-20260811-000000/notes/module/portainer",
            json={"note": "too early"},
            headers={"X-Reviewer": "alice"},
        )
        self.assertEqual(response.status_code, 409)

    def test_note_ownership_validation(self):
        run_id = self.make_review_ready_run("WR-20260811-000000")
        self.make_review_ready_run("WR-20260811-000001")
        other_result = self.repo.list_results("WR-20260811-000001")[0]

        bad_module = self.client.put(
            f"/api/runs/{run_id}/notes/module/not_a_module",
            json={"note": "bad"},
            headers={"X-Reviewer": "alice"},
        )
        self.assertEqual(bad_module.status_code, 400)

        wrong_result = self.client.put(
            f"/api/runs/{run_id}/notes/result/{other_result.id}",
            json={"note": "bad"},
            headers={"X-Reviewer": "alice"},
        )
        self.assertEqual(wrong_result.status_code, 400)

        bad_dashboard = self.client.put(
            f"/api/runs/{run_id}/notes/splunk/not_a_dashboard",
            json={"note": "bad"},
            headers={"X-Reviewer": "alice"},
        )
        self.assertEqual(bad_dashboard.status_code, 400)

    def test_general_notes_must_be_enabled(self):
        run_id = self.make_review_ready_run()
        disabled = copy.deepcopy(self.config)
        disabled["rules"]["review"]["general_notes_enabled"] = False
        app = create_app()
        app.dependency_overrides[dependencies.get_repository] = lambda: self.repo
        app.dependency_overrides[dependencies.get_config] = lambda: disabled
        app.dependency_overrides[dependencies.get_evidence_manager] = lambda: self.evidence
        client = TestClient(app)

        response = client.put(
            f"/api/runs/{run_id}/notes/general",
            json={"note": "general"},
            headers={"X-Reviewer": "alice"},
        )
        self.assertEqual(response.status_code, 400)

    def test_review_ui_loads_saved_notes_and_finalizes(self):
        run_id = self.make_review_ready_run()
        result = self.repo.list_results(run_id)[0]
        self.client.put(
            f"/api/runs/{run_id}/notes/module/portainer",
            json={"note": "module note"},
            headers={"X-Reviewer": "alice"},
        )
        self.client.put(
            f"/api/runs/{run_id}/notes/result/{result.id}",
            json={"note": "result note"},
            headers={"X-Reviewer": "alice"},
        )
        self.client.put(
            f"/api/runs/{run_id}/notes/splunk/system_health",
            json={"note": "splunk note"},
            headers={"X-Reviewer": "alice"},
        )
        self.save_required_dashboard_notes(run_id)
        self.client.put(
            f"/api/runs/{run_id}/notes/general",
            json={"note": "general note"},
            headers={"X-Reviewer": "alice"},
        )

        page = self.client.get(f"/runs/{run_id}/review")
        self.assertEqual(page.status_code, 200)
        html = page.text
        self.assertIn("data-note-endpoint", html)
        self.assertIn("module note", html)
        self.assertIn("result note", html)
        self.assertIn("splunk note", html)
        self.assertIn("general note", html)
        self.assertIn('value="APPROVE"', html)
        self.assertIn('value="REJECT"', html)

        with patch.dict(os.environ, {"WEEKEND_REPORT_AUTH_MODE": "development"}):
            final = self.client.post(
                f"/api/runs/{run_id}/finalize",
                json={"decision": "APPROVE"},
                headers={"X-Reviewer": "alice"},
            )
        self.assertEqual(final.status_code, 200, final.text)
        self.assertEqual(self.repo.get_run(run_id).state, RunState.APPROVED)

    def test_run_overview_is_summary_first(self):
        run_id = self.make_review_ready_run("WR-20260811-000010")

        page = self.client.get(f"/runs/{run_id}")
        self.assertEqual(page.status_code, 200, page.text)
        html = page.text
        self.assertIn("Run Overview", html)
        self.assertIn("Status Summary", html)
        self.assertIn("Site Summary", html)
        self.assertIn("Module Summary", html)
        self.assertIn("Important Findings", html)
        self.assertIn("Show detailed check results", html)
        self.assertNotIn("<table", html.lower())

    def test_specialized_module_pages_render_readable_layouts(self):
        run_id = self.make_review_ready_run("WR-20260811-000011")

        portainer = self.client.get(f"/runs/{run_id}/portainer")
        self.assertEqual(portainer.status_code, 200, portainer.text)
        self.assertIn("Two-Site Comparison", portainer.text)
        self.assertIn("Detailed service checks", portainer.text)
        self.assertIn("Reviewer note for this check", portainer.text)

        rabbitmq = self.client.get(f"/runs/{run_id}/rabbitmq")
        self.assertEqual(rabbitmq.status_code, 200, rabbitmq.text)
        self.assertIn("Queues", rabbitmq.text)
        self.assertIn("Exchanges", rabbitmq.text)
        self.assertIn("Bindings", rabbitmq.text)
        self.assertIn("Node Alarms", rabbitmq.text)

        recording = self.client.get(f"/runs/{run_id}/recording")
        self.assertEqual(recording.status_code, 200, recording.text)
        self.assertIn("Baseline WebApp N", recording.text)
        self.assertIn("Selected existing non-recording device", recording.text)
        self.assertIn("WebApp restored to N", recording.text)

        infrastructure = self.client.get(f"/runs/{run_id}/infrastructure")
        self.assertEqual(infrastructure.status_code, 200, infrastructure.text)
        self.assertIn("Reachability", infrastructure.text)
        self.assertIn("Filesystem Usage", infrastructure.text)
        self.assertIn("Chrony/NTP", infrastructure.text)

    def test_splunk_review_page_uses_dashboard_cards(self):
        run_id = self.make_review_ready_run("WR-20260811-000012")
        self.client.put(
            f"/api/runs/{run_id}/notes/splunk/system_health",
            json={"note": "dashboard reviewed"},
            headers={"X-Reviewer": "alice"},
        )

        page = self.client.get(f"/runs/{run_id}/splunk")
        self.assertEqual(page.status_code, 200, page.text)
        self.assertIn("OPEN ALL DASHBOARDS", page.text)
        self.assertIn("dashboard-card", page.text)
        self.assertIn("Open Dashboard", page.text)
        self.assertIn("dashboard reviewed", page.text)

    def test_review_page_has_explicit_final_confirmation_and_no_tables(self):
        run_id = self.make_review_ready_run("WR-20260811-000013")

        page = self.client.get(f"/runs/{run_id}/review")
        self.assertEqual(page.status_code, 200, page.text)
        html = page.text
        self.assertIn("Automated findings are immutable.", html)
        self.assertIn("Reviewer notes do not convert FAIL/WARNING/ERROR to PASS.", html)
        self.assertIn("data-finalize-confirmation", html)
        self.assertIn('value="APPROVE"', html)
        self.assertIn('value="REJECT"', html)
        self.assertNotIn("<table", html.lower())

    def test_frontend_feedback_uses_toasts_not_browser_alerts(self):
        script = Path("app/web/static/app.js").read_text(encoding="utf-8")
        self.assertNotIn("alert(", script)
        self.assertIn("data-toast-region", Path("app/web/templates/base.html").read_text())

    def test_production_html_ui_flow_saves_notes_and_approve_reject(self):
        env = {
            "WEEKEND_REPORT_AUTH_MODE": "production",
            "WEEKEND_REPORT_AUTH_PROVIDER": "trusted_header",
            "WEEKEND_REPORT_AUTH_TRUSTED_HEADER": "X-Authenticated-User",
            "WEEKEND_REPORT_AUTHORIZED_REVIEWERS": "alice",
            "WEEKEND_REPORT_CSRF_SIGNING_KEY": "test-signing-key",
            "WEEKEND_REPORT_APP_VERSION": "test-version",
            "WEEKEND_REPORT_BUILD_ID": "test-build",
        }
        with patch.dict(os.environ, env):
            run_id = self.make_review_ready_run("WR-20260811-000100")
            result = self.repo.list_results(run_id)[0]
            headers = {"X-Authenticated-User": "alice"}
            page = self.client.get(f"/runs/{run_id}/review", headers=headers)
            self.assertEqual(page.status_code, 200, page.text)
            token = self.extract_csrf(page.text)
            mutation_headers = {**headers, "X-CSRF-Token": token}

            self.assertEqual(
                self.client.put(
                    f"/api/runs/{run_id}/notes/module/portainer",
                    json={"note": "production module note"},
                    headers=mutation_headers,
                ).status_code,
                200,
            )
            self.assertEqual(
                self.client.put(
                    f"/api/runs/{run_id}/notes/result/{result.id}",
                    json={"note": "production result note"},
                    headers=mutation_headers,
                ).status_code,
                200,
            )
            for dashboard in self.config["splunk_dashboards"]["dashboards"]:
                self.assertEqual(
                    self.client.put(
                        f"/api/runs/{run_id}/notes/splunk/{dashboard['id']}",
                        json={"note": f"production splunk {dashboard['id']}"},
                        headers=mutation_headers,
                    ).status_code,
                    200,
                )
            self.assertEqual(
                self.client.put(
                    f"/api/runs/{run_id}/notes/general",
                    json={"note": "production general note"},
                    headers=mutation_headers,
                ).status_code,
                200,
            )

            final = self.client.post(
                f"/api/runs/{run_id}/finalize",
                json={"decision": "APPROVE"},
                headers=mutation_headers,
            )
            self.assertEqual(final.status_code, 200, final.text)
            self.assertEqual(self.repo.get_run(run_id).state, RunState.APPROVED)

            pdf = self.client.get(
                final.json()["final_pdf_url"],
                headers=headers,
            )
            self.assertEqual(pdf.status_code, 200)
            self.assertEqual(pdf.headers["content-type"], "application/pdf")
            self.assertEqual(self.client.get(final.json()["final_pdf_url"]).status_code, 401)

            reject_run_id = self.make_review_ready_run("WR-20260811-000101")
            reject_page = self.client.get(f"/runs/{reject_run_id}/review", headers=headers)
            reject_token = self.extract_csrf(reject_page.text)
            reject = self.client.post(
                f"/api/runs/{reject_run_id}/finalize",
                json={"decision": "REJECT"},
                headers={**headers, "X-CSRF-Token": reject_token},
            )
            self.assertEqual(reject.status_code, 200, reject.text)
            self.assertEqual(self.repo.get_run(reject_run_id).state, RunState.REJECTED)

    def test_production_read_and_final_pdf_require_auth(self):
        env = {
            "WEEKEND_REPORT_AUTH_MODE": "production",
            "WEEKEND_REPORT_AUTH_PROVIDER": "trusted_header",
            "WEEKEND_REPORT_AUTH_TRUSTED_HEADER": "X-Authenticated-User",
            "WEEKEND_REPORT_AUTHORIZED_REVIEWERS": "alice",
            "WEEKEND_REPORT_CSRF_SIGNING_KEY": "test-signing-key",
            "WEEKEND_REPORT_APP_VERSION": "test-version",
            "WEEKEND_REPORT_BUILD_ID": "test-build",
        }
        with patch.dict(os.environ, env):
            self.assertEqual(self.client.get("/").status_code, 401)
            self.assertEqual(self.client.get("/healthz").status_code, 200)
            run_id = self.make_review_ready_run("WR-20260811-000200")
            self.assertEqual(self.client.get(f"/api/runs/{run_id}/evidence").status_code, 401)

    def test_final_pdf_route_rejects_unsafe_recorded_path(self):
        run_id = self.make_review_ready_run("WR-20260811-000300")
        self.repo.set_final_pdf(
            run_id,
            state=RunState.APPROVED,
            reviewer="reviewer",
            decision="APPROVE",
            pdf_path="../secret.pdf",
            checksum="checksum",
        )
        response = self.client.get(f"/api/runs/{run_id}/final-pdf")
        self.assertEqual(response.status_code, 400)

    def extract_csrf(self, html: str) -> str:
        match = re.search(r'name="weekend-report-csrf-token" content="([^"]+)"', html)
        self.assertIsNotNone(match, html)
        assert match is not None
        return match.group(1)


if __name__ == "__main__":
    unittest.main()
