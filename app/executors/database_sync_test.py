from __future__ import annotations

from typing import Any, Protocol, TypedDict


class DatabaseSyncStructuredResult(TypedDict, total=False):
    site: str
    target: str
    create_success: bool
    replication_after_create: bool
    delete_success: bool
    replication_after_delete: bool
    cleanup_complete: bool
    errors: list[dict[str, Any]]
    evidence: dict[str, Any]


class DatabaseSyncFunction(Protocol):
    def __call__(self, config: dict[str, Any]) -> DatabaseSyncStructuredResult:
        ...


def run_database_sync_test(config: dict[str, Any]) -> dict[str, Any]:
    """Adapter boundary for the approved existing database sync function.

    The project intentionally does not reimplement the organization's temp-table
    algorithm. Insert the approved function behind this interface so it returns
    the structured contract used by the validator:
    create_success, replication_after_create, delete_success,
    replication_after_delete, cleanup_complete, and errors.
    """

    raise NotImplementedError(
        "Approved database sync function is not configured. Provide it behind "
        "app.executors.database_sync_test.run_database_sync_test before live runs."
    )
