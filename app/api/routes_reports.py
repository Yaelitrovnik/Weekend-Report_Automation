from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.api.dependencies import (
    get_config,
    get_evidence_manager,
    get_mutating_reviewer,
    get_repository,
    get_reviewer,
)
from app.domain import ReviewDecision
from app.evidence.paths import ensure_under
from app.reporting.snapshot import finalize_run
from app.review.finalization import FinalizationReadinessError

router = APIRouter()
RepoDep = Annotated[Any, Depends(get_repository)]
EvidenceDep = Annotated[Any, Depends(get_evidence_manager)]
ConfigDep = Annotated[dict[str, Any], Depends(get_config)]
ReviewerDep = Annotated[str, Depends(get_mutating_reviewer)]
ReadReviewerDep = Annotated[str, Depends(get_reviewer)]


@router.post("/api/runs/{run_id}/finalize")
def finalize(
    run_id: str,
    payload: dict[str, Any],
    repo: RepoDep,
    evidence: EvidenceDep,
    config: ConfigDep,
    reviewer: ReviewerDep,
):
    try:
        decision = ReviewDecision(payload["decision"])
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
    reviewer: ReadReviewerDep,
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
