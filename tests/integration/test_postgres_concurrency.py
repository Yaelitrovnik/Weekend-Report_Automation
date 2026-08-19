from __future__ import annotations

import os
import threading
import unittest
from urllib.parse import urlparse

from app.database.repository import Repository
from app.orchestrator.lock import DuplicateActiveRun


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
