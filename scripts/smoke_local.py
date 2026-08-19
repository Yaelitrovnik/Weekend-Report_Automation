from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.loader import load_config_dir
from app.database.repository import Repository
from app.evidence.manager import EvidenceManager
from app.orchestrator.run_context import RunContext
from app.orchestrator.runner import OrchestratorRunner
from app.runtime_identity import current_runtime_identity


def main() -> int:
    config = load_config_dir("tests/fixtures/config_valid")
    with tempfile.TemporaryDirectory() as tmp:
        repo = Repository(f"sqlite:///{Path(tmp) / 'db.sqlite'}")
        evidence = EvidenceManager(Path(tmp) / "evidence")
        try:
            identity = current_runtime_identity(config)
            run = repo.create_run(
                started_by="smoke",
                run_id="WR-20260811-000000",
                application_version=identity.application_version,
                build_id=identity.build_id,
                git_commit=identity.git_commit,
                config_version=identity.configuration_hash,
            )
            claimed = repo.claim_next_run("smoke-worker")
            assert claimed and claimed.run_id == run.run_id
            OrchestratorRunner().run(RunContext(run.run_id, config, repo, evidence))
            assert repo.get_run(run.run_id).state.value == "REVIEW_READY"
        finally:
            repo.close()
    print("safe local smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
