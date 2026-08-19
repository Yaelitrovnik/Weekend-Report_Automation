from __future__ import annotations

import copy
import unittest

from app.config.loader import load_config_dir
from app.database.repository import Repository
from app.domain import CheckResult, CheckStatus, NoteScope, ReviewDecision, ReviewNote
from app.review.finalization import validate_finalization_readiness


class FinalizationReadinessTests(unittest.TestCase):
    def setUp(self):
        self.repo = Repository("sqlite:///:memory:")
        self.addCleanup(self.repo.close)
        self.config = load_config_dir("tests/fixtures/config_valid")

    def single_module_config(self, module: str = "portainer") -> dict:
        config = copy.deepcopy(self.config)
        for name, rule in config["rules"]["modules"].items():
            rule["enabled"] = name == module
            rule["required"] = name == module
        return config

    def make_review_ready_run(
        self,
        *,
        run_id: str = "WR-20260811-000000",
        result_status: CheckStatus = CheckStatus.PASS,
        module: str = "portainer",
        metadata: dict | None = None,
    ) -> str:
        self.repo.create_run(started_by="tester", run_id=run_id)
        self.repo.claim_next_run("worker")
        self.repo.add_result(
            CheckResult(
                run_id,
                module,
                f"{module}.check",
                result_status,
                "fixture finding",
                site="site1",
                metadata=metadata or {},
            )
        )
        self.repo.mark_review_ready(run_id, result_status)
        return run_id

    def save_dashboard_reviews(self, run_id: str, config: dict) -> None:
        for dashboard in config["splunk_dashboards"]["dashboards"]:
            self.repo.save_note(
                ReviewNote(
                    run_id,
                    NoteScope.SPLUNK_DASHBOARD,
                    "reviewer",
                    f"reviewed {dashboard['id']}",
                    dashboard_id=dashboard["id"],
                )
            )

    def test_approve_reports_missing_required_review_inputs(self):
        config = self.single_module_config()
        run_id = self.make_review_ready_run()
        errors = validate_finalization_readiness(
            self.repo,
            config,
            run_id,
            ReviewDecision.APPROVE,
        )
        self.assertTrue(any("Module portainer requires" in error for error in errors))
        self.assertTrue(any("Splunk dashboard system_health" in error for error in errors))

    def test_approve_allows_after_required_review_inputs(self):
        config = self.single_module_config()
        run_id = self.make_review_ready_run()
        self.repo.save_note(
            ReviewNote(run_id, NoteScope.MODULE, "reviewer", "module note", module="portainer")
        )
        self.save_dashboard_reviews(run_id, config)
        errors = validate_finalization_readiness(
            self.repo,
            config,
            run_id,
            ReviewDecision.APPROVE,
        )
        self.assertEqual(errors, [])

    def test_manual_review_requires_result_acknowledgment(self):
        config = self.single_module_config("doctor")
        config["rules"]["review"]["required_module_notes"] = []
        config["splunk_dashboards"]["dashboards"] = []
        run_id = self.make_review_ready_run(
            run_id="WR-20260811-000001",
            module="doctor",
            result_status=CheckStatus.MANUAL_REVIEW,
        )
        result = self.repo.list_results(run_id)[0]
        errors = validate_finalization_readiness(
            self.repo,
            config,
            run_id,
            ReviewDecision.APPROVE,
        )
        self.assertTrue(any("MANUAL_REVIEW" in error for error in errors))
        self.repo.save_note(
            ReviewNote(run_id, NoteScope.RESULT, "reviewer", "acknowledged", result_id=result.id)
        )
        self.assertEqual(
            validate_finalization_readiness(self.repo, config, run_id, ReviewDecision.APPROVE),
            [],
        )

    def test_status_policy_blocks_and_requires_notes(self):
        config = self.single_module_config()
        config["rules"]["review"]["required_module_notes"] = []
        config["splunk_dashboards"]["dashboards"] = []
        fail_run = self.make_review_ready_run(
            run_id="WR-20260811-000002",
            result_status=CheckStatus.FAIL,
        )
        fail_errors = validate_finalization_readiness(
            self.repo,
            config,
            fail_run,
            ReviewDecision.APPROVE,
        )
        self.assertTrue(any("FAIL" in error and "blocked" in error for error in fail_errors))

        warning_config = self.single_module_config()
        warning_config["rules"]["review"]["required_module_notes"] = []
        warning_config["splunk_dashboards"]["dashboards"] = []
        warning_config["rules"]["review"]["approval_status_policy"]["WARNING"] = "REQUIRE_NOTE"
        warning_run = self.make_review_ready_run(
            run_id="WR-20260811-000003",
            result_status=CheckStatus.WARNING,
        )
        warning_result = self.repo.list_results(warning_run)[0]
        warning_errors = validate_finalization_readiness(
            self.repo,
            warning_config,
            warning_run,
            ReviewDecision.APPROVE,
        )
        self.assertTrue(any("REQUIRE_NOTE" in error for error in warning_errors))
        self.repo.save_note(
            ReviewNote(
                warning_run,
                NoteScope.RESULT,
                "reviewer",
                "warning accepted",
                result_id=warning_result.id,
            )
        )
        self.assertEqual(
            validate_finalization_readiness(
                self.repo,
                warning_config,
                warning_run,
                ReviewDecision.APPROVE,
            ),
            [],
        )

    def test_recording_cleanup_requirement_requires_acknowledgment(self):
        config = self.single_module_config("recording")
        config["rules"]["review"]["required_module_notes"] = []
        config["rules"]["review"]["approval_status_policy"]["FAIL"] = "REQUIRE_NOTE"
        config["splunk_dashboards"]["dashboards"] = []
        run_id = self.make_review_ready_run(
            run_id="WR-20260811-000004",
            module="recording",
            result_status=CheckStatus.FAIL,
            metadata={"cleanup_required": True},
        )
        result = self.repo.list_results(run_id)[0]
        errors = validate_finalization_readiness(
            self.repo,
            config,
            run_id,
            ReviewDecision.APPROVE,
        )
        self.assertTrue(any("Recording cleanup" in error for error in errors))
        self.repo.save_note(
            ReviewNote(
                run_id,
                NoteScope.RESULT,
                "reviewer",
                "cleanup verified manually",
                result_id=result.id,
            )
        )
        self.assertEqual(
            validate_finalization_readiness(self.repo, config, run_id, ReviewDecision.APPROVE),
            [],
        )

    def test_reject_policy_can_block_reject(self):
        config = self.single_module_config()
        config["rules"]["review"]["reject_allowed"] = False
        run_id = self.make_review_ready_run()
        errors = validate_finalization_readiness(
            self.repo,
            config,
            run_id,
            ReviewDecision.REJECT,
        )
        self.assertEqual(errors, ["REJECT is disabled by rules.review.reject_allowed."])


if __name__ == "__main__":
    unittest.main()
