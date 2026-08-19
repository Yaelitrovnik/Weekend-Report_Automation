from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.api.dependencies import get_evidence_manager, get_repository, get_reviewer

router = APIRouter()
RepoDep = Annotated[Any, Depends(get_repository)]
EvidenceDep = Annotated[Any, Depends(get_evidence_manager)]
ReviewerDep = Annotated[str, Depends(get_reviewer)]


@router.get("/api/runs/{run_id}/evidence")
def list_evidence(run_id: str, repo: RepoDep, reviewer: ReviewerDep):
    return repo.list_evidence(run_id)


@router.get("/api/evidence/{path:path}")
def get_evidence(path: str, repo: RepoDep, evidence: EvidenceDep, reviewer: ReviewerDep):
    try:
        record = repo.get_evidence_by_path(path)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="evidence is not registered") from exc
    try:
        target = evidence.absolute(record.path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="unsafe evidence path") from exc
    if not target.exists():
        raise HTTPException(status_code=404, detail="evidence not found")
    return FileResponse(str(target))
