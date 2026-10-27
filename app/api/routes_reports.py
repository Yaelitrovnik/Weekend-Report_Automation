from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.api.dependencies import (
    get_config,
    get_evidence_manager,
    get_mutation_guard,
    get_repository,
)
from app.domain import ReviewDecision
from app.evidence.paths import ensure_under
from app.reporting.snapshot import finalize_run
from app.review.finalization import FinalizationReadinessError

router = APIRouter()
RepoDep = Annotated[Any, Depends(get_repository)]
EvidenceDep = Annotated[Any, Depends(get_evidence_manager)]
ConfigDep = Annotated[dict[str, Any], Depends(get_config)]
MutationGuardDep = Annotated[None, Depends(get_mutation_guard)]


def _manual_reviewer_name(payload: dict[str, Any]) -> str:
    value = payload.get("reviewer")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Reviewer name is required")
    reviewer = value.strip()
    if len(reviewer) > 100:
        raise ValueError("Reviewer name must be 100 characters or fewer")
    if any(char in reviewer for char in ("\r", "\n", "\x00")):
        raise ValueError("Reviewer name contains unsupported characters")
    return reviewer


@router.post("/api/runs/{run_id}/finalize")
def finalize(
    run_id: str,
    payload: dict[str, Any],
    repo: RepoDep,
    evidence: EvidenceDep,
    config: ConfigDep,
    _guard: MutationGuardDep,
):
    try:
        decision = ReviewDecision(payload["decision"])
        reviewer = _manual_reviewer_name(payload)
        snapshot = finalize_run(repo, evidence, config, run_id, reviewer, decision)
    except FinalizationReadinessError as exc:
        raise HTTPException(status_code=400, detail=exc.errors) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "run_id": run_id,
        "decision": decision.value,
        "snapshot_results": len(snapshot["results"]),
        "final_pdf_url": f"/api/runs/{run_id}/final-pdf",
    }


@router.get("/api/runs/{run_id}/final-pdf")
def final_pdf(
    run_id: str,
    repo: RepoDep,
    evidence: EvidenceDep,
):
    run = repo.get_run(run_id)
    if not run.final_pdf_path:
        raise HTTPException(status_code=404, detail="final PDF is not available for this run")
    expected_prefix = Path("runs") / run_id / "final"
    recorded = Path(run.final_pdf_path)
    if recorded.is_absolute() or ".." in recorded.parts or expected_prefix not in recorded.parents:
        raise HTTPException(status_code=400, detail="recorded final PDF path is unsafe")
    try:
        target = ensure_under(evidence.root, evidence.root / recorded)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="recorded final PDF path is unsafe") from exc
    if not target.exists():
        raise HTTPException(status_code=404, detail="recorded final PDF file is missing")
    return FileResponse(str(target), media_type="application/pdf", filename=target.name)
