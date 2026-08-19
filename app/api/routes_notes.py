from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import get_config, get_mutating_reviewer, get_repository
from app.orchestrator.lock import InvalidRunTransition
from app.review.notes import (
    NoteValidationError,
)
from app.review.notes import (
    save_general_note as persist_general_note,
)
from app.review.notes import (
    save_module_note as persist_module_note,
)
from app.review.notes import (
    save_result_note as persist_result_note,
)
from app.review.notes import (
    save_splunk_note as persist_splunk_note,
)

router = APIRouter()
RepoDep = Annotated[Any, Depends(get_repository)]
ConfigDep = Annotated[dict[str, Any], Depends(get_config)]
ReviewerDep = Annotated[str, Depends(get_mutating_reviewer)]


@router.put("/api/runs/{run_id}/notes/module/{module}")
def save_module_note(
    run_id: str,
    module: str,
    payload: dict[str, Any],
    repo: RepoDep,
    reviewer: ReviewerDep,
):
    try:
        note_id = persist_module_note(repo, run_id, module, reviewer, payload.get("note", ""))
    except InvalidRunTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except NoteValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": note_id}


@router.put("/api/runs/{run_id}/notes/result/{result_id}")
def save_result_note(
    run_id: str,
    result_id: int,
    payload: dict[str, Any],
    repo: RepoDep,
    reviewer: ReviewerDep,
):
    try:
        note_id = persist_result_note(repo, run_id, result_id, reviewer, payload.get("note", ""))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown result: {result_id}") from exc
    except InvalidRunTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except NoteValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": note_id}


@router.put("/api/runs/{run_id}/notes/splunk/{dashboard_id}")
def save_splunk_note(
    run_id: str,
    dashboard_id: str,
    payload: dict[str, Any],
    repo: RepoDep,
    config: ConfigDep,
    reviewer: ReviewerDep,
):
    try:
        note_id = persist_splunk_note(
            repo, config, run_id, dashboard_id, reviewer, payload.get("note", "")
        )
    except InvalidRunTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except NoteValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": note_id}


@router.put("/api/runs/{run_id}/notes/general")
def save_general_note(
    run_id: str,
    payload: dict[str, Any],
    repo: RepoDep,
    config: ConfigDep,
    reviewer: ReviewerDep,
):
    try:
        note_id = persist_general_note(repo, config, run_id, reviewer, payload.get("note", ""))
    except InvalidRunTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except NoteValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": note_id}
