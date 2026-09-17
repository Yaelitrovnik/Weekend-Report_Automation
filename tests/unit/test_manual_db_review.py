from __future__ import annotations

import unittest
from typing import cast
from unittest.mock import patch

from app.database.repository import Repository
from app.domain import (
    CheckStatus,
    ManualDBReview,
    ManualDBReviewResult,
    RunState,
    to_jsonable,
)
from app.orchestrator.lock import InvalidRunTransition
from app.review.manual_db import (
    ManualDBReviewValidationError,
    record_manual_db_review,
    validate_manual_db_review,
)


class ManualDBReviewTests(unittest.TestCase):

    def setUp(self) -> None:
        self.repo = Repository("sqlite:///:memory:")
        self.addCleanup(self.repo.close)

    def make_review_ready_run(
        self,
        run_id: str = "WR-20260909-120000",
    ) -> str:
        self.repo.create_run(
            started_by="tester",
            run_id=run_id,
        )

        claimed = self.repo.claim_next_run("worker")
        self.assertIsNotNone(claimed)

        self.repo.mark_review_ready(
            run_id,
            CheckStatus.PASS,
        )

        return run_id

    def make_review(
        self,
        *,
        result: ManualDBReviewResult = ManualDBReviewResult.PASS,
        comment: str = "Database synchronization verified.",
        reviewer: str = "alice",
        run_id: str = "WR-20260909-120000",
        display_name: str = "Database Synchronization Check",
        script_path: str = (
            "C:\\Scripts\\DatabaseSync\\database_sync_check.ps1"
        ),
        reviewed_at: str | None = None,
    ) -> ManualDBReview:
        return ManualDBReview(
            run_id=run_id,
            display_name=display_name,
            script_path=script_path,
            result=result,
            comment=comment,
            reviewer=reviewer,
            reviewed_at=reviewed_at,
        )

    def test_manual_db_result_values_are_stable(self):
        self.assertEqual(
            ManualDBReviewResult.PASS.value,
            "PASS",
        )
        self.assertEqual(
            ManualDBReviewResult.FAIL.value,
            "FAIL",
        )
        self.assertEqual(
            ManualDBReviewResult.NOT_RUN.value,
            "NOT RUN",
        )

    def test_all_supported_results_are_valid(self):
        for result in ManualDBReviewResult:
            with self.subTest(result=result):
                validate_manual_db_review(
                    self.make_review(result=result)
                )

    def test_comment_is_required_for_every_result(self):
        for result in ManualDBReviewResult:
            with self.subTest(result=result):
                with self.assertRaises(
                    ManualDBReviewValidationError
                ):
                    validate_manual_db_review(
                        self.make_review(
                            result=result,
                            comment="   ",
                        )
                    )

    def test_reviewer_is_required(self):
        with self.assertRaises(
            ManualDBReviewValidationError
        ):
            validate_manual_db_review(
                self.make_review(reviewer="")
            )

    def test_run_id_is_required(self):
        with self.assertRaises(
            ManualDBReviewValidationError
        ):
            validate_manual_db_review(
                self.make_review(run_id="")
            )

    def test_display_name_is_required(self):
        with self.assertRaises(
            ManualDBReviewValidationError
        ):
            validate_manual_db_review(
                self.make_review(display_name="")
            )

    def test_script_path_must_be_absolute_windows_path(self):
        with self.assertRaises(
            ManualDBReviewValidationError
        ):
            validate_manual_db_review(
                self.make_review(
                    script_path="database_sync_check.ps1",
                )
            )

    def test_script_path_must_reference_ps1(self):
        with self.assertRaises(
            ManualDBReviewValidationError
        ):
            validate_manual_db_review(
                self.make_review(
                    script_path=(
                        "C:\\Scripts\\DatabaseSync\\"
                        "database_sync_check.txt"
                    ),
                )
            )

    def test_invalid_result_is_rejected(self):
        invalid_result = cast(
            ManualDBReviewResult,
            "INVALID",
        )

        with self.assertRaises(
            ManualDBReviewValidationError
        ):
            validate_manual_db_review(
                self.make_review(
                    result=invalid_result
                )
            )

    def test_manual_db_review_is_json_serializable(self):
        review = self.make_review(
            result=ManualDBReviewResult.NOT_RUN,
            comment="Script could not be executed.",
        )

        payload = to_jsonable(review)

        self.assertEqual(
            payload["result"],
            "NOT RUN",
        )
        self.assertEqual(
            payload["script_path"],
            (
                "C:\\Scripts\\DatabaseSync\\"
                "database_sync_check.ps1"
            ),
        )
        self.assertEqual(
            payload["comment"],
            "Script could not be executed.",
        )

    def test_manual_db_review_save_and_read_round_trip(
        self,
    ):
        run_id = self.make_review_ready_run()

        with patch(
            "app.database.repository.iso_now",
            return_value="2026-09-09T10:00:00+00:00",
        ):
            review_id = record_manual_db_review(
                self.repo,
                self.make_review(run_id=run_id),
            )

        saved = self.repo.get_manual_db_review(
            run_id
        )

        self.assertIsNotNone(saved)
        assert saved is not None

        self.assertEqual(
            saved.id,
            review_id,
        )
        self.assertEqual(
            saved.run_id,
            run_id,
        )
        self.assertEqual(
            saved.result,
            ManualDBReviewResult.PASS,
        )
        self.assertEqual(
            saved.comment,
            "Database synchronization verified.",
        )
        self.assertEqual(
            saved.reviewer,
            "alice",
        )
        self.assertEqual(
            saved.reviewed_at,
            "2026-09-09T10:00:00+00:00",
        )

    def test_second_manual_db_save_updates_same_record(
        self,
    ):
        run_id = self.make_review_ready_run()

        with patch(
            "app.database.repository.iso_now",
            return_value="2026-09-09T10:00:00+00:00",
        ):
            first_id = record_manual_db_review(
                self.repo,
                self.make_review(run_id=run_id),
            )

        with patch(
            "app.database.repository.iso_now",
            return_value="2026-09-09T10:05:00+00:00",
        ):
            second_id = record_manual_db_review(
                self.repo,
                self.make_review(
                    run_id=run_id,
                    result=ManualDBReviewResult.FAIL,
                    comment="Synchronization failed.",
                    reviewer="bob",
                ),
            )

        self.assertEqual(
            second_id,
            first_id,
        )

        saved = self.repo.get_manual_db_review(
            run_id
        )

        self.assertIsNotNone(saved)
        assert saved is not None

        self.assertEqual(
            saved.result,
            ManualDBReviewResult.FAIL,
        )
        self.assertEqual(
            saved.comment,
            "Synchronization failed.",
        )
        self.assertEqual(
            saved.reviewer,
            "bob",
        )
        self.assertEqual(
            saved.reviewed_at,
            "2026-09-09T10:05:00+00:00",
        )

    def test_manual_db_review_cannot_be_saved_before_review_ready(
        self,
    ):
        run_id = "WR-20260909-120001"

        self.repo.create_run(
            started_by="tester",
            run_id=run_id,
        )

        with self.assertRaises(
            InvalidRunTransition
        ):
            record_manual_db_review(
                self.repo,
                self.make_review(run_id=run_id),
            )

    def test_manual_db_review_cannot_be_edited_after_finalization(
        self,
    ):
        run_id = self.make_review_ready_run()

        record_manual_db_review(
            self.repo,
            self.make_review(run_id=run_id),
        )

        self.repo.set_final_pdf(
            run_id,
            state=RunState.APPROVED,
            reviewer="alice",
            decision="APPROVE",
            pdf_path="final.pdf",
            checksum="test-checksum",
        )

        with self.assertRaises(
            InvalidRunTransition
        ):
            record_manual_db_review(
                self.repo,
                self.make_review(
                    run_id=run_id,
                    comment=(
                        "attempted change after approval"
                    ),
                ),
            )

    def test_manual_db_review_timestamp_is_generated_server_side(
        self,
    ):
        run_id = self.make_review_ready_run()

        with patch(
            "app.database.repository.iso_now",
            return_value="2026-09-09T11:00:00+00:00",
        ):
            record_manual_db_review(
                self.repo,
                self.make_review(
                    run_id=run_id,
                    reviewed_at=(
                        "1900-01-01T00:00:00+00:00"
                    ),
                ),
            )

        saved = self.repo.get_manual_db_review(
            run_id
        )

        self.assertIsNotNone(saved)
        assert saved is not None

        self.assertEqual(
            saved.reviewed_at,
            "2026-09-09T11:00:00+00:00",
        )

    def test_get_manual_db_review_returns_none_when_not_recorded(
        self,
    ):
        run_id = self.make_review_ready_run()

        self.assertIsNone(
            self.repo.get_manual_db_review(
                run_id
            )
        )


if __name__ == "__main__":
    unittest.main()