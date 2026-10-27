from __future__ import annotations

from typing import Any

from app.config.schema import VALID_MODULES
from app.domain import NoteScope, ReviewNote


class NoteValidationError(ValueError):
    pass


VALID_REVIEW_MODULES = VALID_MODULES | {"site_parity"}


def save_module_note(repository, run_id: str, module: str, reviewer: str, note_text: str) -> int:
    repository.require_note_editable(run_id)
    if module not in VALID_REVIEW_MODULES:
        raise NoteValidationError(f"unknown module: {module}")
    return repository.save_note(
        ReviewNote(run_id, NoteScope.MODULE, reviewer, note_text, module=module)
    )


def save_result_note(repository, run_id: str, result_id: int, reviewer: str, note_text: str) -> int:
    repository.require_note_editable(run_id)
    result = repository.get_result(result_id)
    if result.run_id != run_id:
        raise NoteValidationError(f"result {result_id} does not belong to run {run_id}")
    return repository.save_note(
        ReviewNote(run_id, NoteScope.RESULT, reviewer, note_text, result_id=result_id)
    )


def save_splunk_note(
    repository,
    config: dict[str, Any],
    run_id: str,
    dashboard_id: str,
    reviewer: str,
    note_text: str,
    *,
    reviewed: bool = False,
) -> int:
    repository.require_note_editable(run_id)
    dashboard_ids = {
        dashboard.get("id")
        for dashboard in config.get("splunk_dashboards", {}).get("dashboards", [])
        if isinstance(dashboard, dict)
    }
    if dashboard_id not in dashboard_ids:
        raise NoteValidationError(f"unknown Splunk dashboard: {dashboard_id}")
    return repository.save_note(
        ReviewNote(
            run_id,
            NoteScope.SPLUNK_DASHBOARD,
            reviewer,
            note_text,
            dashboard_id=dashboard_id,
            reviewed=reviewed,
        )
    )


def save_general_note(
    repository,
    config: dict[str, Any],
    run_id: str,
    reviewer: str,
    note_text: str,
) -> int:
    repository.require_note_editable(run_id)
    if not general_notes_enabled(config):
        raise NoteValidationError("general notes are not enabled by configuration")
    return repository.save_note(ReviewNote(run_id, NoteScope.GENERAL, reviewer, note_text))


def general_notes_enabled(config: dict[str, Any]) -> bool:
    review = config.get("rules", {}).get("review", {})
    if not isinstance(review, dict):
        return False
    return bool(review.get("general_notes_enabled") or review.get("general_note_enabled"))


def notes_by_key(notes: list[ReviewNote]) -> dict[str, str]:
    keyed: dict[str, str] = {}
    for note in notes:
        key = note_key(note)
        if key:
            keyed[key] = note.note
    return keyed


def note_key(note: ReviewNote) -> str | None:
    if note.scope == NoteScope.MODULE and note.module:
        return f"module:{note.module}"
    if note.scope == NoteScope.RESULT and note.result_id is not None:
        return f"result:{note.result_id}"
    if note.scope == NoteScope.SPLUNK_DASHBOARD and note.dashboard_id:
        return f"splunk:{note.dashboard_id}"
    if note.scope == NoteScope.GENERAL:
        return "general"
    return None
