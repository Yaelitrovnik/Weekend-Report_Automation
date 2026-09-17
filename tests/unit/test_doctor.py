from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

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
from app.evidence.manager import EvidenceManager
from app.orchestrator.aggregation import aggregate_status
from app.orchestrator.run_context import RunContext
from app.review.finalization import validate_finalization_readiness
from app.review.manual_db import record_manual_db_review
from app.validators.doctor import DoctorValidator
from app.validators.site_parity import SiteParityValidator
from tests.fixtures.config_factory import load_fixture_config


class DoctorTests(unittest.TestCase):
    def setUp(self) -> None:
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
        return copy.deepcopy(self.config["doctor"]["doctor"]["fixture_actual"])

    def validate(self, actual: dict) -> list[CheckResult]:
        return DoctorValidator().validate(actual, self.config, self.ctx)

    def single_doctor_config(self) -> dict:
        config = copy.deepcopy(self.config)
        for module, rule in config["rules"]["modules"].items():
            rule["enabled"] = module == "doctor"
            rule["required"] = module == "doctor"
        config["splunk_dashboards"]["dashboards"] = []
        config["rules"]["review"]["required_module_notes"] = []
        config["rules"]["review"]["general_note_required"] = False
        return config

    def test_dynamic_discovered_services_healthy_on_both_sites_pass(self):
        actual = self.fixture_actual()
        actual["sites"]["site1"]["services"]["service-18"] = {"healthy": True}
        actual["sites"]["site2"]["services"]["service-18"] = {"healthy": True}

        results = self.validate(actual)
        service_results = [
            result for result in results if result.check_id == "doctor.service.health"
        ]
        presence_results = [
            result for result in results if result.check_id == "doctor.service.exists"
        ]
        self.assertEqual(len(service_results), 36)
        self.assertEqual(len(presence_results), 36)
        self.assertEqual([result.status for result in service_results], [CheckStatus.PASS] * 36)
        self.assertEqual([result.status for result in presence_results], [CheckStatus.PASS] * 36)
        self.assertEqual(_module_status(results), CheckStatus.PASS)

    def test_unhealthy_service_stays_error_and_module_requires_manual_review(self):
        actual = self.fixture_actual()
        actual["sites"]["site1"]["services"]["service-03"] = {
            "healthy": False,
            "reason": "disk full",
        }
        results = self.validate(actual)
        unhealthy = [
            result
            for result in results
            if result.check_id == "doctor.service.health"
            and result.site == "site1"
            and result.target == "service-03"
        ][0]
        self.assertEqual(unhealthy.status, CheckStatus.ERROR)
        self.assertTrue(unhealthy.metadata["reviewable_health_issue"])
        self.assertEqual(unhealthy.metadata["doctor_error_type"], "health_issue")
        self.assertIn("disk full", unhealthy.message)
        self.assertEqual(_module_status(results), CheckStatus.MANUAL_REVIEW)
        self.assertEqual(aggregate_status(results, self.config), CheckStatus.MANUAL_REVIEW)

    def test_service_set_mismatch_fails_cross_site_parity(self):
        actual = self.fixture_actual()
        del actual["sites"]["site2"]["services"]["service-17"]

        results = self.validate(actual)
        self.assertEqual(_module_status(results), CheckStatus.PASS)

        parity = SiteParityValidator().validate(
            {"results": results},
            self.config,
            self.ctx,
        )
        doctor_parity = [
            result for result in parity if result.check_id == "parity.doctor.service_presence"
        ][0]
        self.assertEqual(doctor_parity.status, CheckStatus.FAIL)
        self.assertFalse(doctor_parity.actual["match"])


    def test_doctor_uses_runtime_site_ids(self):
        config = copy.deepcopy(self.config)
        config["sites"]["sites"][0]["id"] = "primary"
        config["sites"]["sites"][1]["id"] = "secondary"
        actual = self.fixture_actual()
        actual["sites"]["primary"] = actual["sites"].pop("site1")
        actual["sites"]["secondary"] = actual["sites"].pop("site2")

        ctx = RunContext(
            self.ctx.run_id,
            config,
            self.repo,
            self.ctx.evidence,
        )
        results = DoctorValidator().validate(actual, config, ctx)

        self.assertEqual(_module_status(results), CheckStatus.PASS)
        self.assertEqual(
            {result.site for result in results if result.check_id == "doctor.service.health"},
            {"primary", "secondary"},
        )

    def test_site_with_no_discovered_services_is_technical_error(self):
        actual = self.fixture_actual()
        actual["sites"]["site2"]["services"] = {}

        results = self.validate(actual)

        collection = [
            result
            for result in results
            if result.check_id == "doctor.collection" and result.site == "site2"
        ][0]
        self.assertEqual(collection.status, CheckStatus.ERROR)
        self.assertEqual(collection.metadata["error_code"], "DOCTOR_NO_SERVICES_DISCOVERED")
        self.assertEqual(_module_status(results), CheckStatus.ERROR)

    def test_collection_error_remains_technical_module_error(self):
        actual = {
            "errors": [
                {
                    "site": "site1",
                    "code": "DOCTOR_AUTHENTICATION_ERROR",
                    "message": "auth failed",
                }
            ]
        }
        results = self.validate(actual)
        self.assertEqual(results[0].status, CheckStatus.ERROR)
        self.assertEqual(results[0].metadata["doctor_error_type"], "technical")
        self.assertEqual(_module_status(results), CheckStatus.ERROR)
        self.assertEqual(aggregate_status(results, self.config), CheckStatus.ERROR)

    def test_finalization_exception_requires_module_manual_review_ack(self):
        config = self.single_doctor_config()
        run_id = "WR-20260811-000010"
        self.repo.create_run(started_by="tester", run_id=run_id)
        self.repo.claim_next_run("worker")
        health_id = self.repo.add_result(
            CheckResult(
                run_id,
                "doctor",
                "doctor.service.health",
                CheckStatus.ERROR,
                "DOCTOR service is unhealthy: disk full",
                site="site1",
                target="service-03",
                metadata={
                    "doctor_error_type": "health_issue",
                    "reviewable_health_issue": True,
                },
            )
        )
        module_id = self.repo.add_result(
            CheckResult(
                run_id,
                "doctor",
                "doctor.module_status",
                CheckStatus.MANUAL_REVIEW,
                "DOCTOR automated services require human review",
                metadata={"doctor_manual_review_path": True},
            )
        )
        self.repo.mark_review_ready(run_id, CheckStatus.MANUAL_REVIEW)
        errors = validate_finalization_readiness(
            self.repo,
            config,
            run_id,
            ReviewDecision.APPROVE,
        )
        record_manual_db_review(
            self.repo,
            ManualDBReview(
                run_id=run_id,
                display_name="Database Synchronization Check",
                script_path=(
                    "C:\\Scripts\\DatabaseSync\\"
                    "database_sync_check.ps1"
                ),
                result=ManualDBReviewResult.PASS,
                comment="Database synchronization verified.",
                reviewer="reviewer",
            ),
        )
        self.assertTrue(any("MANUAL_REVIEW" in error for error in errors))
        self.assertTrue(any("ERROR" in error and "blocked" in error for error in errors))

        self.repo.save_note(
            ReviewNote(
                run_id,
                NoteScope.RESULT,
                "reviewer",
                "reviewed unhealthy DOCTOR service and accept manual-review finalization",
                result_id=module_id,
            )
        )
        self.assertEqual(
            validate_finalization_readiness(self.repo, config, run_id, ReviewDecision.APPROVE),
            [],
        )
        self.assertEqual(self.repo.get_result(health_id).status, CheckStatus.ERROR)

    def test_finalization_exception_does_not_cover_technical_errors(self):
        config = self.single_doctor_config()
        run_id = "WR-20260811-000011"
        self.repo.create_run(started_by="tester", run_id=run_id)
        self.repo.claim_next_run("worker")
        self.repo.add_result(
            CheckResult(
                run_id,
                "doctor",
                "doctor.collection",
                CheckStatus.ERROR,
                "DOCTOR_AUTHENTICATION_ERROR: auth failed",
                site="site1",
                metadata={
                    "doctor_error_type": "technical",
                    "error_code": "DOCTOR_AUTHENTICATION_ERROR",
                },
            )
        )
        module_id = self.repo.add_result(
            CheckResult(
                run_id,
                "doctor",
                "doctor.module_status",
                CheckStatus.MANUAL_REVIEW,
                "DOCTOR automated services require human review",
            )
        )
        self.repo.mark_review_ready(run_id, CheckStatus.ERROR)
        self.repo.save_note(
            ReviewNote(
                run_id,
                NoteScope.RESULT,
                "reviewer",
                "manual-review module acknowledged",
                result_id=module_id,
            )
        )
        errors = validate_finalization_readiness(
            self.repo,
            config,
            run_id,
            ReviewDecision.APPROVE,
        )
        self.assertTrue(any("ERROR" in error and "blocked" in error for error in errors))

    def test_unhealthy_service_without_reason_is_technical_error(self):
        actual = self.fixture_actual()
        actual["sites"]["site1"]["services"]["service-03"] = {
            "healthy": False,
        }

        results = self.validate(actual)

        unhealthy = [
            result
            for result in results
            if result.check_id == "doctor.service.health"
            and result.site == "site1"
            and result.target == "service-03"
        ][0]

        self.assertEqual(unhealthy.status, CheckStatus.ERROR)
        self.assertEqual(unhealthy.metadata["doctor_error_type"], "technical")
        self.assertEqual(
            unhealthy.metadata["error_code"],
            "DOCTOR_UNHEALTHY_REASON_MISSING",
        )
        self.assertNotIn("reviewable_health_issue", unhealthy.metadata)
        self.assertEqual(_module_status(results), CheckStatus.ERROR)
        self.assertEqual(aggregate_status(results, self.config), CheckStatus.ERROR)


def _module_status(results: list[CheckResult]) -> CheckStatus:
    return [result.status for result in results if result.check_id == "doctor.module_status"][0]


if __name__ == "__main__":
    unittest.main()
