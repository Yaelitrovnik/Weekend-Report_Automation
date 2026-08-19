from __future__ import annotations

from app.database.repository import Repository


def update_heartbeat(
    repository: Repository, run_id: str, worker_id: str, current_module: str | None = None
) -> None:
    repository.heartbeat(run_id, current_module=current_module, worker_id=worker_id)
