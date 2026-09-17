from __future__ import annotations

import os
import threading
import unittest
from urllib.parse import urlparse

from app.database.repository import Repository
from app.domain import (
    CheckStatus,
    ManualDBReview,
    ManualDBReviewResult,
)
from app.orchestrator.lock import DuplicateActiveRun
from app.review.manual_db import record_manual_db_review


@unittest.skipUnless(
    os.getenv("WEEKEND_REPORT_TEST_POSTGRES_URL"),
    "set WEEKEND_REPORT_TEST_POSTGRES_URL to run PostgreSQL concurrency tests",
)
class PostgreSQLConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.url = os.environ["WEEKEND_REPORT_TEST_POSTGRES_URL"]
        self.assert_disposable_url()
        self.repo = Repository(self.url)
        self.reset_state()
        self.addCleanup(self.repo.close)
        self.addCleanup(self.reset_state)

    def assert_disposable_url(self) -> None:
        db_name = urlparse(self.url).path.rsplit("/", maxsplit=1)[-1].lower()
        explicit = os.getenv("WEEKEND_REPORT_TEST_POSTGRES_DISPOSABLE") == "1"
        if not explicit and "test" not in db_name:
            self.skipTest(
                "PostgreSQL concurrency tests require a disposable test database name "
                "or WEEKEND_REPORT_TEST_POSTGRES_DISPOSABLE=1"
            )

    def reset_state(self) -> None:
        self.repo._execute(
            "TRUNCATE review_notes, evidence, results, runs RESTART IDENTITY CASCADE"
        )
        self.repo._execute(
            "UPDATE run_lock SET active_run_id=NULL, updated_at=NOW() WHERE name='weekend_report'"
        )

    def test_manual_db_review_upsert_works_in_postgres(self):
        run_id = "WR-20260909-130000"
        self.repo.create_run(
            started_by="tester",
            run_id=run_id,
        )
        self.repo.claim_next_run("worker")
        self.repo.mark_review_ready(
            run_id,
            CheckStatus.PASS,
        )
        first_id = record_manual_db_review(
            self.repo,
            ManualDBReview(
                run_id=run_id,
                display_name="Database Synchronization Check",
                script_path=(
                    "C:\\Scripts\\DatabaseSync\\database_sync_check.ps1"
                ),
                result=ManualDBReviewResult.PASS,
                comment="Synchronization verified.",
                reviewer="alice",
            ),
        )
        second_id = record_manual_db_review(
            self.repo,
            ManualDBReview(
                run_id=run_id,
                display_name="Database Synchronization Check",
                script_path=(
                    "C:\\Scripts\\DatabaseSync\\database_sync_check.ps1"
                ),
                result=ManualDBReviewResult.FAIL,
                comment="Synchronization failed on second review.",
                reviewer="bob",
            ),
        )
        self.assertEqual(first_id, second_id)
        saved = self.repo.get_manual_db_review(run_id)
        self.assertIsNotNone(saved)
        assert saved is not None
        self.assertEqual(
            saved.result,
            ManualDBReviewResult.FAIL,
        )
        self.assertEqual(
            saved.comment,
            "Synchronization failed on second review.",
        )
        self.assertEqual(saved.reviewer, "bob")
        self.assertIsNotNone(saved.reviewed_at)

    def test_duplicate_run_prevention_is_atomic(self):
        successes: list[str] = []
        duplicates: list[str] = []
        lock = threading.Lock()

        def create(index: int) -> None:
            repo = Repository(self.url)
            try:
                repo.create_run(started_by="tester", run_id=f"WR-20260811-{index:06d}")
                with lock:
                    successes.append(str(index))
            except DuplicateActiveRun:
                with lock:
                    duplicates.append(str(index))
            finally:
                repo.close()

        threads = [threading.Thread(target=create, args=(idx,)) for idx in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(successes), 1)
        self.assertEqual(len(duplicates), 7)

    def test_only_one_worker_claims_run(self):
        self.repo.create_run(started_by="tester", run_id="WR-20260811-000000")
        claimed: list[str] = []
        lock = threading.Lock()

        def claim(worker: str) -> None:
            repo = Repository(self.url)
            try:
                run = repo.claim_next_run(worker)
                if run:
                    with lock:
                        claimed.append(worker)
            finally:
                repo.close()

        threads = [threading.Thread(target=claim, args=(f"worker-{idx}",)) for idx in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(claimed), 1)


if __name__ == "__main__":
    unittest.main()
