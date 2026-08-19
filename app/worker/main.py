from __future__ import annotations

import os
import socket
import time

from app.config.loader import load_config_dir
from app.config.validation import validate_config
from app.database.repository import Repository
from app.evidence.manager import EvidenceManager
from app.orchestrator.run_context import RunContext
from app.orchestrator.runner import OrchestratorRunner


def main() -> None:
    worker_id = f"{socket.gethostname()}-{os.getpid()}"
    repo = Repository(
        os.getenv("WEEKEND_REPORT_DATABASE_URL", "sqlite:///data/weekend-report.sqlite")
    )
    config = load_config_dir(os.getenv("WEEKEND_REPORT_CONFIG_DIR", "config"))
    evidence = EvidenceManager(os.getenv("WEEKEND_REPORT_EVIDENCE_ROOT", "runs"))
    while True:
        recover_stale_runs(repo, config, worker_id)
        run = repo.claim_next_run(worker_id)
        if run:
            preflight = validate_config(config)
            if not preflight.ok:
                repo.mark_failed(run.run_id, "; ".join(preflight.lines()))
                continue
            try:
                OrchestratorRunner().run(RunContext(run.run_id, config, repo, evidence))
            except Exception as exc:
                repo.mark_failed(run.run_id, str(exc))
        time.sleep(float(os.getenv("WEEKEND_REPORT_WORKER_POLL_SECONDS", "2")))


def recover_stale_runs(repo: Repository, config: dict, worker_id: str) -> None:
    recovery = config.get("rules", {}).get("recovery", {})
    timeout = recovery.get("heartbeat_timeout_seconds") if isinstance(recovery, dict) else None
    if isinstance(timeout, int | float) and timeout > 0:
        repo.recover_stale_runs(
            heartbeat_timeout_seconds=int(timeout),
            worker_id=worker_id,
        )


if __name__ == "__main__":
    main()
