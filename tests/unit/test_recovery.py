from __future__ import annotations

import unittest
from datetime import timedelta

from app.database.repository import Repository
from app.domain import CheckStatus, RunState
from app.orchestrator.lock import DuplicateActiveRun
from app.time_utils import iso_now, utcnow


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.repo = Repository("sqlite:///:memory:")
        self.addCleanup(self.repo.close)

    def stale_running_run(self, run_id: str, module: str):
        self.repo.create_run(started_by="tester", run_id=run_id)
        self.repo.claim_next_run("worker-a")
        old = (utcnow() - timedelta(hours=2)).isoformat()
        self.repo._execute(
            "UPDATE runs SET current_module=?, last_heartbeat=?, updated_at=? WHERE run_id=?",
            (module, old, iso_now(), run_id),
        )

    def test_non_recording_stale_run_fails_without_replay(self):
        self.stale_running_run("WR-20260811-000000", "portainer")
        recovered = self.repo.recover_stale_runs(heartbeat_timeout_seconds=60, worker_id="worker-b")
        self.assertEqual(len(recovered), 1)
        run = self.repo.get_run("WR-20260811-000000")
        self.assertEqual(run.state, RunState.FAILED)
        self.assertEqual(run.automation_status, CheckStatus.ERROR)
        self.assertIn("without replay", run.current_module)

    def test_recording_stale_run_requires_manual_recovery(self):
        self.stale_running_run("WR-20260811-000000", "recording")
        recovered = self.repo.recover_stale_runs(heartbeat_timeout_seconds=60, worker_id="worker-b")
        self.assertEqual(len(recovered), 1)
        run = self.repo.get_run("WR-20260811-000000")
        self.assertEqual(run.state, RunState.RECOVERY_REQUIRED)
        self.assertEqual(run.automation_status, CheckStatus.ERROR)
        self.assertIn("manual cleanup", run.current_module)

    def test_recovery_required_blocks_until_explicitly_resolved(self):
        self.stale_running_run("WR-20260811-000000", "recording")
        self.repo.recover_stale_runs(heartbeat_timeout_seconds=60, worker_id="worker-b")
        with self.assertRaises(DuplicateActiveRun):
            self.repo.create_run(started_by="tester", run_id="WR-20260811-000001")

        resolved = self.repo.resolve_recovery(
            "WR-20260811-000000",
            reviewer="operator",
            note="verified Recording cleanup in target environment",
        )
        self.assertEqual(resolved.state, RunState.FAILED)
        self.assertIn("no Recording replay", resolved.current_module)
        new_run = self.repo.create_run(started_by="tester", run_id="WR-20260811-000001")
        self.assertEqual(new_run.state, RunState.CREATED)


class PostgreSQLLockSqlTests(unittest.TestCase):
    def test_postgresql_lock_queries_are_explicit(self):
        repo = object.__new__(Repository)
        repo.backend = "postgres"
        self.assertIn("FOR UPDATE", repo._run_lock_sql())
        self.assertIn("FOR UPDATE SKIP LOCKED", repo._claim_candidate_sql())


if __name__ == "__main__":
    unittest.main()
