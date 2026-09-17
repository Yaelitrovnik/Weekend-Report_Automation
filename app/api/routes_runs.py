from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.api.dependencies import get_config, get_mutation_guard, get_repository
from app.config.validation import validate_config
from app.domain import SYSTEM_ACTOR
from app.orchestrator.lock import DuplicateActiveRun, InvalidRunTransition
from app.runtime_identity import current_runtime_identity
from app.security import csrf_token_for_template

router = APIRouter()
templates = Jinja2Templates(directory="app/web/templates")
RepoDep = Annotated[Any, Depends(get_repository)]
ConfigDep = Annotated[dict[str, Any], Depends(get_config)]
MutationGuardDep = Annotated[None, Depends(get_mutation_guard)]


@router.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    repo: RepoDep,
    config: ConfigDep,
):
    preflight = validate_config(config)
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "request": request,
            "runs": repo.list_runs(),
            "preflight": preflight,
            "csrf_token": csrf_token_for_template(),
        },
    )


@router.post("/api/runs")
def create_run(
    config: ConfigDep,
    repo: RepoDep,
    _guard: MutationGuardDep,
):
    preflight = validate_config(config)
    if not preflight.ok:
        raise HTTPException(status_code=400, detail=preflight.lines())
    identity = current_runtime_identity(config)
    try:
        run = repo.create_run(
            started_by=SYSTEM_ACTOR,
            application_version=identity.application_version,
            build_id=identity.build_id,
            git_commit=identity.git_commit,
            config_version=identity.configuration_hash,
        )
    except DuplicateActiveRun as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"run_id": run.run_id, "state": run.state}


@router.post("/runs")
def create_run_form(
    config: ConfigDep,
    repo: RepoDep,
    _guard: MutationGuardDep,
):
    preflight = validate_config(config)
    if not preflight.ok:
        return RedirectResponse("/", status_code=303)
    identity = current_runtime_identity(config)
    try:
        run = repo.create_run(
            started_by=SYSTEM_ACTOR,
            application_version=identity.application_version,
            build_id=identity.build_id,
            git_commit=identity.git_commit,
            config_version=identity.configuration_hash,
        )
    except DuplicateActiveRun:
        return RedirectResponse("/", status_code=303)
    return RedirectResponse(f"/runs/{run.run_id}", status_code=303)


@router.post("/api/runs/{run_id}/recovery/resolve")
def resolve_recovery(
    run_id: str,
    payload: dict[str, Any],
    repo: RepoDep,
    _guard: MutationGuardDep,
):
    note = str(payload.get("note", "")).strip()
    if not note:
        raise HTTPException(
            status_code=400,
            detail="Recovery resolution requires a note describing the verified cleanup.",
        )
    try:
        run = repo.resolve_recovery(run_id, reviewer=SYSTEM_ACTOR, note=note)
    except InvalidRunTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"run_id": run.run_id, "state": run.state.value}
