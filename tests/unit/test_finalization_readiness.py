from __future__ import annotations

import copy
import unittest

from app.database.repository import Repository
from app.domain import (
    CheckResult,
    CheckStatus,
    ManualDBReview,
    ManualDBReviewResult,
    NoteScope,
    ReviewDecision,
    ReviewNote,
)
from app.review.finalization import validate_finalization_readiness
from app.review.manual_db import record_manual_db_review
from tests.fixtures.config_factory import load_fixture_config


class FinalizationReadinessTests(unittest.TestCase):
    def setUp(self):
        self.repo = Repository("sqlite:///:memory:")
        self.addCleanup(self.repo.close)
        self.config = load_fixture_config()

    def single_module_config(self, module: str = "portainer") -> dict:
        config = copy.deepcopy(self.config)
        for name, rule in config["rules"]["modules"].items():
            rule["enabled"] = name == module
            rule["required"] = name == module
        config["rules"]["review"]["required_module_notes"] = [
            module
        ]
        config["rules"]["review"]["general_note_required"] = False
        return config

    def save_manual_db_result(
        self,
        run_id: str,
        result: ManualDBReviewResult,
        *,
        comment: str = "Manual database synchronization check completed.",
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
                comment=comment,
                reviewer="reviewer",
            ),
        )

    def db_policy_only_config(self) -> dict:
        config = self.single_module_config()
        config["rules"]["review"][
            "required_module_notes"
        ] = []
        config["rules"]["review"][
            "general_note_required"
        ] = False
        config["splunk_dashboards"][
            "dashboards"
        ] = []
        return config

    def make_review_ready_run(
        self,
        *,
        run_id: str = "WR-20260811-000000",
        result_status: CheckStatus = CheckStatus.PASS,
        module: str = "portainer",
        metadata: dict | None = None,
        manual_db_result: ManualDBReviewResult | None = (
            ManualDBReviewResult.PASS
        ),
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
        self.repo.mark_review_ready(
            run_id,
            result_status,
        )
        if manual_db_result is not None:
            self.save_manual_db_result(
                run_id,
                manual_db_result,
            )
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
                    reviewed=True,
                )
            )

    def test_manual_db_review_required_before_approve(self):
        config = self.db_policy_only_config()
        run_id = self.make_review_ready_run(
            run_id="WR-20260910-000001",
            manual_db_result=None,
        )
        errors = validate_finalization_readiness(
            self.repo,
            config,
            run_id,
            ReviewDecision.APPROVE,
        )
        self.assertTrue(
            any(
                "must be completed before finalization"
                in error
                for error in errors
            )
        )

    def test_manual_db_review_required_before_reject(self):
        config = self.db_policy_only_config()
        run_id = self.make_review_ready_run(
            run_id="WR-20260910-000002",
            manual_db_result=None,
        )
        errors = validate_finalization_readiness(
            self.repo,
            config,
            run_id,
            ReviewDecision.REJECT,
        )
        self.assertTrue(
            any(
                "must be completed before finalization"
                in error
                for error in errors
            )
        )

    def test_manual_db_pass_allows_approve(self):
        config = self.db_policy_only_config()
        run_id = self.make_review_ready_run(
            run_id="WR-20260910-000003",
            manual_db_result=ManualDBReviewResult.PASS,
        )
        self.assertEqual(
            validate_finalization_readiness(
                self.repo,
                config,
                run_id,
                ReviewDecision.APPROVE,
            ),
            [],
        )

    def test_manual_db_pass_blocks_reject(self):
        config = self.db_policy_only_config()
        run_id = self.make_review_ready_run(
            run_id="WR-20260910-000004",
            manual_db_result=ManualDBReviewResult.PASS,
        )
        errors = validate_finalization_readiness(
            self.repo,
            config,
            run_id,
            ReviewDecision.REJECT,
        )
        self.assertTrue(
            any(
                "REJECT is not permitted"
                in error
                for error in errors
            )
        )

    def test_manual_db_fail_blocks_approve(self):
        config = self.db_policy_only_config()
        run_id = self.make_review_ready_run(
            run_id="WR-20260910-000005",
            manual_db_result=ManualDBReviewResult.FAIL,
        )
        errors = validate_finalization_readiness(
            self.repo,
            config,
            run_id,
            ReviewDecision.APPROVE,
        )
        self.assertTrue(
            any(
                "APPROVE requires"
                in error
                and "PASS"
                in error
                for error in errors
            )
        )

    def test_manual_db_fail_allows_reject(self):
        config = self.db_policy_only_config()
        run_id = self.make_review_ready_run(
            run_id="WR-20260910-000006",
            manual_db_result=ManualDBReviewResult.FAIL,
        )
        self.assertEqual(
            validate_finalization_readiness(
                self.repo,
                config,
                run_id,
                ReviewDecision.REJECT,
            ),
            [],
        )

    def test_manual_db_not_run_blocks_approve(self):
        config = self.db_policy_only_config()
        run_id = self.make_review_ready_run(
            run_id="WR-20260910-000007",
            manual_db_result=ManualDBReviewResult.NOT_RUN,
        )
        errors = validate_finalization_readiness(
            self.repo,
            config,
            run_id,
            ReviewDecision.APPROVE,
        )
        self.assertTrue(
            any(
                "APPROVE requires"
                in error
                and "NOT RUN"
                in error
                for error in errors
            )
        )

    def test_manual_db_not_run_allows_reject(self):
        config = self.db_policy_only_config()
        run_id = self.make_review_ready_run(
            run_id="WR-20260910-000008",
            manual_db_result=ManualDBReviewResult.NOT_RUN,
        )
        self.assertEqual(
            validate_finalization_readiness(
                self.repo,
                config,
                run_id,
                ReviewDecision.REJECT,
            ),
            [],
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

    def test_required_splunk_review_is_not_satisfied_by_note_text_alone(self):
        config = self.single_module_config()
        config["rules"]["review"]["required_module_notes"] = []
        config["splunk_dashboards"]["dashboards"] = [
            {
                "id": "system_health",
                "display_name": "System Health",
                "url": "https://example.invalid/splunk/system-health",
                "required_review": True,
                "note_required": False,
            }
        ]
        run_id = self.make_review_ready_run()
        self.repo.save_note(
            ReviewNote(
                run_id,
                NoteScope.SPLUNK_DASHBOARD,
                "reviewer",
                "I opened the dashboard",
                dashboard_id="system_health",
            )
        )
        errors = validate_finalization_readiness(
            self.repo,
            config,
            run_id,
            ReviewDecision.APPROVE,
        )
        self.assertTrue(any("must be reviewed and saved" in error for error in errors))

    def test_required_splunk_review_without_note_text_allows_when_note_not_required(self):
        config = self.single_module_config()
        config["rules"]["review"]["required_module_notes"] = []
        config["splunk_dashboards"]["dashboards"] = [
            {
                "id": "system_health",
                "display_name": "System Health",
                "url": "https://example.invalid/splunk/system-health",
                "required_review": True,
                "note_required": False,
            }
        ]
        run_id = self.make_review_ready_run()
        self.repo.save_note(
            ReviewNote(
                run_id,
                NoteScope.SPLUNK_DASHBOARD,
                "reviewer",
                "",
                dashboard_id="system_health",
                reviewed=True,
            )
        )
        self.assertEqual(
            validate_finalization_readiness(self.repo, config, run_id, ReviewDecision.APPROVE),
            [],
        )

    def test_optional_splunk_dashboard_can_require_note_without_review(self):
        config = self.single_module_config()
        config["rules"]["review"]["required_module_notes"] = []
        config["splunk_dashboards"]["dashboards"] = [
            {
                "id": "errors",
                "display_name": "Errors",
                "url": "https://example.invalid/splunk/errors",
                "required_review": False,
                "note_required": True,
                "order": 1,
            }
        ]
        run_id = self.make_review_ready_run(
            run_id="WR-20260916-000001",
        )
        # Optional review does not require reviewed=True,
        # but note_required=True must still block an empty note.
        self.repo.save_note(
            ReviewNote(
                run_id,
                NoteScope.SPLUNK_DASHBOARD,
                "reviewer",
                "",
                dashboard_id="errors",
                reviewed=False,
            )
        )
        errors = validate_finalization_readiness(
            self.repo,
            config,
            run_id,
            ReviewDecision.APPROVE,
        )
        self.assertTrue(
            any(
                "Splunk dashboard errors requires a non-empty note."
                in error
                for error in errors
            ),
            errors,
        )
        # A non-empty note satisfies note_required even though
        # the dashboard itself remains optional and unreviewed.
        self.repo.save_note(
            ReviewNote(
                run_id,
                NoteScope.SPLUNK_DASHBOARD,
                "reviewer",
                "Errors dashboard checked for relevant findings.",
                dashboard_id="errors",
                reviewed=False,
            )
        )
        self.assertEqual(
            validate_finalization_readiness(
                self.repo,
                config,
                run_id,
                ReviewDecision.APPROVE,
            ),
            [],
        )

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
        run_id = self.make_review_ready_run(manual_db_result=ManualDBReviewResult.FAIL,)
        errors = validate_finalization_readiness(
            self.repo,
            config,
            run_id,
            ReviewDecision.REJECT,
        )
        self.assertEqual(errors, ["REJECT is disabled by rules.review.reject_allowed."])


if __name__ == "__main__":
    unittest.main()
