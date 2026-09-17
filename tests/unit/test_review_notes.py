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
from app.database.repository import Repository
from app.domain import SYSTEM_ACTOR, CheckResult, CheckStatus, RunState
from app.evidence.manager import EvidenceManager
from app.web.main import create_app
from tests.fixtures.config_factory import load_fixture_config


class ReviewNoteApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Repository("sqlite:///:memory:")
        self.addCleanup(self.repo.close)
        self.config = load_fixture_config()
        self.evidence = EvidenceManager(Path(self.tmp.name) / "evidence")
        app = create_app()

        app.dependency_overrides[dependencies.get_repository] = lambda: self.repo
        app.dependency_overrides[dependencies.get_config] = lambda: self.config
        app.dependency_overrides[dependencies.get_evidence_manager] = lambda: self.evidence

        self.client = TestClient(app)
        self.addCleanup(self.client.close)

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
                json={"note": f"reviewed {dashboard['id']}", "reviewed": True},
            )

    def save_required_module_notes(
        self,
        run_id: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        request_headers = headers or {}
        for module in self.config["rules"]["review"]["required_module_notes"]:
            response = self.client.put(
                f"/api/runs/{run_id}/notes/module/{module}",
                json={"note": f"module note for {module}"},
                headers=request_headers,
            )
            self.assertEqual(
                response.status_code,
                200,
                response.text,
            )

    def test_notes_are_blocked_before_review_ready(self):
        self.repo.create_run(started_by="tester", run_id="WR-20260811-000000")
        response = self.client.put(
            "/api/runs/WR-20260811-000000/notes/module/portainer",
            json={"note": "too early"},
        )
        self.assertEqual(response.status_code, 409)

    def test_note_ownership_validation(self):
        run_id = self.make_review_ready_run("WR-20260811-000000")
        self.make_review_ready_run("WR-20260811-000001")
        other_result = self.repo.list_results("WR-20260811-000001")[0]

        bad_module = self.client.put(
            f"/api/runs/{run_id}/notes/module/not_a_module",
            json={"note": "bad"},
        )
        self.assertEqual(bad_module.status_code, 400)

        wrong_result = self.client.put(
            f"/api/runs/{run_id}/notes/result/{other_result.id}",
            json={"note": "bad"},
        )
        self.assertEqual(wrong_result.status_code, 400)

        bad_dashboard = self.client.put(
            f"/api/runs/{run_id}/notes/splunk/not_a_dashboard",
            json={"note": "bad"},
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
        )
        self.assertEqual(response.status_code, 400)

    def test_review_ui_loads_saved_notes_and_finalizes(self):
        run_id = self.make_review_ready_run()
        result = self.repo.list_results(run_id)[0]
        self.save_required_module_notes(run_id)
        self.client.put(
            f"/api/runs/{run_id}/notes/result/{result.id}",
            json={"note": "result note"},
        )
        self.client.put(
            f"/api/runs/{run_id}/notes/splunk/system_health",
            json={"note": "splunk note", "reviewed": True},
        )
        self.save_required_dashboard_notes(run_id)
        self.client.put(
            f"/api/runs/{run_id}/notes/general",
            json={"note": "general note"},
        )

        manual_db = self.client.put(
            f"/api/runs/{run_id}/manual-db-review",
            json={
                "result": "PASS",
                "comment": "Database synchronization verified.",
            },
        )
        self.assertEqual(
            manual_db.status_code,
            200,
            manual_db.text,
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

        final = self.client.post(
            f"/api/runs/{run_id}/finalize",
            json={"decision": "APPROVE", "reviewer": "Alice Operator"},
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
        self.assertIn("Node Resources", rabbitmq.text)
        self.assertNotIn("Exchanges", rabbitmq.text)
        self.assertNotIn("Bindings", rabbitmq.text)
        self.assertNotIn("Node Alarms", rabbitmq.text)

        recording = self.client.get(f"/runs/{run_id}/recording")
        self.assertEqual(recording.status_code, 200, recording.text)
        self.assertIn("Four runtime baselines", recording.text)
        self.assertIn("Selected existing non-recording device", recording.text)
        self.assertIn("All four observations restored", recording.text)

        infrastructure = self.client.get(f"/runs/{run_id}/infrastructure")
        self.assertEqual(infrastructure.status_code, 200, infrastructure.text)
        self.assertIn("Reachability", infrastructure.text)
        self.assertIn("Filesystem Usage", infrastructure.text)
        self.assertIn("Chrony/NTP", infrastructure.text)

    def test_splunk_review_page_uses_dashboard_cards(self):
        dashboards = {
            dashboard["id"]: dashboard
            for dashboard in self.config["splunk_dashboards"]["dashboards"]
        }
        dashboards["system_health"].update(
            {
                "required_review": True,
                "note_required": True,
                "order": 30,
            }
        )
        dashboards["recording"].update(
            {
                "required_review": False,
                "note_required": False,
                "order": 10,
            }
        )
        dashboards["infrastructure"].update(
            {
                "required_review": True,
                "note_required": False,
                "order": 20,
            }
        )
        dashboards["errors"].update(
            {
                "required_review": False,
                "note_required": True,
                "order": 40,
            }
        )
        self.config["splunk_dashboards"]["open_all"]["include_optional"] = False
        run_id = self.make_review_ready_run(
            "WR-20260811-000012"
        )
        response = self.client.put(
            f"/api/runs/{run_id}/notes/splunk/system_health",
            json={
                "note": "dashboard reviewed",
                "reviewed": True,
            },
        )
        self.assertEqual(
            response.status_code,
            200,
            response.text,
        )
        page = self.client.get(
            f"/runs/{run_id}/splunk"
        )
        self.assertEqual(
            page.status_code,
            200,
            page.text,
        )
        html = page.text
        self.assertIn(
            "OPEN ALL DASHBOARDS",
            html,
        )
        self.assertIn(
            "dashboard-card",
            html,
        )
        self.assertIn(
            "Open Dashboard",
            html,
        )
        self.assertIn(
            "Dashboard reviewed",
            html,
        )
        self.assertIn(
            "dashboard reviewed",
            html,
        )
        # Cards must follow the configured display order.
        recording_position = html.index(
            'name="splunk-recording"'
        )
        infrastructure_position = html.index(
            'name="splunk-infrastructure"'
        )
        system_health_position = html.index(
            'name="splunk-system_health"'
        )
        errors_position = html.index(
            'name="splunk-errors"'
        )
        self.assertLess(
            recording_position,
            infrastructure_position,
        )
        self.assertLess(
            infrastructure_position,
            system_health_position,
        )
        self.assertLess(
            system_health_position,
            errors_position,
        )

        def dashboard_card_html(
            dashboard_id: str,
        ) -> str:
            marker = f'name="splunk-{dashboard_id}"'
            marker_position = html.index(marker)
            card_start = html.rfind(
                '<article class="dashboard-card"',
                0,
                marker_position,
            )
            card_end = html.find(
                "</article>",
                marker_position,
            )
            self.assertNotEqual(
                card_start,
                -1,
            )
            self.assertNotEqual(
                card_end,
                -1,
            )
            return html[
                card_start : card_end + len("</article>")
            ]
        system_health_card = dashboard_card_html(
            "system_health"
        )
        recording_card = dashboard_card_html(
            "recording"
        )
        infrastructure_card = dashboard_card_html(
            "infrastructure"
        )
        errors_card = dashboard_card_html(
            "errors"
        )
        # Review and note requirements must be visible to the operator.
        self.assertIn(
            "Required review",
            system_health_card,
        )
        self.assertIn(
            "Reviewer note",
            system_health_card,
        )
        self.assertIn(
            "(required)",
            system_health_card,
        )
        self.assertIn(
            "Optional",
            recording_card,
        )
        self.assertIn(
            "(optional)",
            recording_card,
        )
        self.assertIn(
            "Required review",
            infrastructure_card,
        )
        self.assertIn(
            "(optional)",
            infrastructure_card,
        )
        self.assertIn(
            "Optional",
            errors_card,
        )
        self.assertIn(
            "(required)",
            errors_card,
        )
        # OPEN ALL excludes optional dashboards when configured to do so.
        self.assertIn(
            "data-dashboard-open-all",
            system_health_card,
        )
        self.assertIn(
            "data-dashboard-open-all",
            infrastructure_card,
        )
        self.assertNotIn(
            "data-dashboard-open-all",
            recording_card,
        )
        self.assertNotIn(
            "data-dashboard-open-all",
            errors_card,
        )

        page = self.client.get(f"/runs/{run_id}/splunk")
        self.assertEqual(page.status_code, 200, page.text)
        self.assertIn("OPEN ALL DASHBOARDS", page.text)
        self.assertIn("dashboard-card", page.text)
        self.assertIn("Open Dashboard", page.text)
        self.assertIn("Dashboard reviewed", page.text)
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
        self.assertIn("data-final-reviewer-name", html)
        self.assertNotIn("<table", html.lower())

    def test_frontend_feedback_uses_toasts_not_browser_alerts(self):
        script = Path("app/web/static/app.js").read_text(encoding="utf-8")
        self.assertNotIn("alert(", script)
        self.assertIn("data-toast-region", Path("app/web/templates/base.html").read_text())

    def test_production_html_ui_flow_saves_notes_and_approve_reject(self):
        env = {
            "WEEKEND_REPORT_CSRF_SIGNING_KEY": "test-signing-key",
            "WEEKEND_REPORT_APP_VERSION": "test-version",
            "WEEKEND_REPORT_BUILD_ID": "test-build",
        }
        with patch.dict(os.environ, env):
            run_id = self.make_review_ready_run("WR-20260811-000100")
            result = self.repo.list_results(run_id)[0]
            page = self.client.get(f"/runs/{run_id}/review")
            self.assertEqual(page.status_code, 200, page.text)
            token = self.extract_csrf(page.text)
            mutation_headers = {"X-CSRF-Token": token}

            # Manual DB review mutation must require CSRF in production.
            no_csrf = self.client.put(
                f"/api/runs/{run_id}/manual-db-review",
                json={
                    "result": "PASS",
                    "comment": "must not save",
                },
            )
            self.assertNotEqual(
                no_csrf.status_code,
                200,
            )
            # The same request succeeds with the page's valid CSRF token.
            manual_db = self.client.put(
                f"/api/runs/{run_id}/manual-db-review",
                json={
                    "result": "PASS",
                    "comment": "Production DB review passed.",
                },
                headers=mutation_headers,
            )
            self.assertEqual(
                manual_db.status_code,
                200,
                manual_db.text,
            )
            self.save_required_module_notes(
                run_id,
                headers=mutation_headers,
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
                        json={
                            "note": f"production splunk {dashboard['id']}",
                            "reviewed": True,
                        },
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
                json={"decision": "APPROVE", "reviewer": "Alice Operator"},
                headers=mutation_headers,
            )
            self.assertEqual(final.status_code, 200, final.text)
            self.assertEqual(self.repo.get_run(run_id).state, RunState.APPROVED)

            pdf = self.client.get(
                final.json()["final_pdf_url"],
            )
            self.assertEqual(pdf.status_code, 200)
            self.assertEqual(pdf.headers["content-type"], "application/pdf")
            self.assertEqual(self.client.get(final.json()["final_pdf_url"]).status_code, 200)

            reject_run_id = self.make_review_ready_run("WR-20260811-000101")
            reject_page = self.client.get(f"/runs/{reject_run_id}/review")
            reject_token = self.extract_csrf(reject_page.text)
            reject_headers = {"X-CSRF-Token": reject_token}
            manual_db_reject = self.client.put(
                f"/api/runs/{reject_run_id}/manual-db-review",
                json={
                    "result": "FAIL",
                    "comment": "Database synchronization failed.",
                },
                headers=reject_headers,
            )
            self.assertEqual(
                manual_db_reject.status_code,
                200,
                manual_db_reject.text,
            )
            reject = self.client.post(
                f"/api/runs/{reject_run_id}/finalize",
                json={"decision": "REJECT", "reviewer": "Alice Operator"},
                headers=reject_headers,
            )
            self.assertEqual(reject.status_code, 200, reject.text)
            self.assertEqual(self.repo.get_run(reject_run_id).state, RunState.REJECTED)

    def test_read_routes_do_not_require_login(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/healthz").status_code, 200)
        run_id = self.make_review_ready_run("WR-20260811-000200")
        self.assertEqual(self.client.get(f"/api/runs/{run_id}/evidence").status_code, 200)

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

    def test_manual_db_review_api_persists_server_controlled_values(self):
        run_id = self.make_review_ready_run(
            "WR-20260909-000400"
        )
        response = self.client.put(
            f"/api/runs/{run_id}/manual-db-review",
            json={
                "result": "PASS",
                "comment": "Synchronization verified.",
                "reviewer": "mallory",
                "script_path": "C:\\Fake\\evil.ps1",
                "display_name": "Fake Check",
                "reviewed_at": "1900-01-01T00:00:00Z",
            },
        )
        self.assertEqual(
            response.status_code,
            200,
            response.text,
        )
        saved = self.repo.get_manual_db_review(run_id)
        self.assertIsNotNone(saved)
        assert saved is not None
        self.assertEqual(saved.reviewer, SYSTEM_ACTOR)
        self.assertEqual(
            saved.display_name,
            "Database Synchronization Check",
        )
        self.assertEqual(
            saved.script_path,
            (
                "C:\\Scripts\\DatabaseSync\\"
                "database_sync_check.ps1"
            ),
        )
        self.assertEqual(
            saved.comment,
            "Synchronization verified.",
        )

    def test_manual_db_review_requires_result_and_comment(self):
        run_id = self.make_review_ready_run(
            "WR-20260909-000401"
        )
        missing_result = self.client.put(
            f"/api/runs/{run_id}/manual-db-review",
            json={"comment": "test"},
        )
        self.assertEqual(
            missing_result.status_code,
            400,
        )
        blank_comment = self.client.put(
            f"/api/runs/{run_id}/manual-db-review",
            json={
                "result": "PASS",
                "comment": "   ",
            },
        )
        self.assertEqual(
            blank_comment.status_code,
            400,
        )

    def test_manual_db_review_is_blocked_before_review_ready(self):
        run_id = "WR-20260909-000402"
        self.repo.create_run(
            started_by="tester",
            run_id=run_id,
        )
        response = self.client.put(
            f"/api/runs/{run_id}/manual-db-review",
            json={
                "result": "PASS",
                "comment": "too early",
            },
        )
        self.assertEqual(
            response.status_code,
            409,
        )

    def test_review_page_shows_manual_db_check(self):
        run_id = self.make_review_ready_run(
            "WR-20260909-000403"
        )
        page = self.client.get(
            f"/runs/{run_id}/review"
        )
        self.assertEqual(
            page.status_code,
            200,
            page.text,
        )
        html = page.text
        self.assertIn(
            "Database Synchronization Check",
            html,
        )
        self.assertIn(
            "C:\\Scripts\\DatabaseSync\\database_sync_check.ps1",
            html,
        )
        self.assertIn(
            "Copy PowerShell Command",
            html,
        )
        self.assertIn(
            'value="PASS"',
            html,
        )
        self.assertIn(
            'value="FAIL"',
            html,
        )
        self.assertIn(
            'value="NOT RUN"',
            html,
        )
        self.assertIn(
            "DB check comment — required",
            html,
        )

    def test_review_page_loads_saved_manual_db_review(self):
        run_id = self.make_review_ready_run(
            "WR-20260909-000404"
        )
        response = self.client.put(
            f"/api/runs/{run_id}/manual-db-review",
            json={
                "result": "FAIL",
                "comment": "Site 2 did not synchronize.",
            },
        )
        self.assertEqual(
            response.status_code,
            200,
            response.text,
        )
        page = self.client.get(
            f"/runs/{run_id}/review"
        )
        self.assertEqual(page.status_code, 200)
        self.assertIn(
            "Site 2 did not synchronize.",
            page.text,
        )


if __name__ == "__main__":
    unittest.main()
