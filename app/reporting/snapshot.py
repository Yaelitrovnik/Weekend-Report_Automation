from __future__ import annotations

from pathlib import Path
from typing import Any

from app.domain import ReviewDecision, RunState, to_jsonable
from app.evidence.manager import EvidenceManager
from app.orchestrator.aggregation import aggregate_status, module_summaries, site_summaries
from app.reporting.final_pdf import render_pdf_under
from app.review.finalization import enforce_finalization_readiness
from app.time_utils import iso_now


def build_snapshot(
    repository, run_id: str, config: dict[str, Any], reviewer: str, decision: ReviewDecision
) -> dict[str, Any]:
    run = repository.get_run(run_id)
    results = repository.list_results(run_id)
    evidence = repository.list_evidence(run_id)
    notes = repository.list_notes(run_id)
    dashboards = config.get("splunk_dashboards", {}).get("dashboards", [])
    parity_results = [
        result
        for result in results
        if result.module == "site_parity" or result.metadata.get("parity_only") is True
    ]
    snapshot = {
        "snapshot_version": 1,
        "created_at": iso_now(),
        "run": to_jsonable(run),
        "results": to_jsonable(results),
        "evidence": to_jsonable(evidence),
        "notes": to_jsonable(notes),
        "overall_status": aggregate_status(results, config).value,
        "site_summaries": to_jsonable(site_summaries(results, config)),
        "module_summaries": to_jsonable(module_summaries(results, config)),
        "parity_summaries": [
            {
                "check_id": result.check_id,
                "target": result.target,
                "status": result.status.value,
                "expected": to_jsonable(result.expected),
                "actual": to_jsonable(result.actual),
                "message": result.message,
                "evidence": list(result.evidence),
            }
            for result in parity_results
        ],
        "splunk_dashboards": dashboards,
        "review": {"reviewer": reviewer, "decision": decision.value, "confirmed_at": iso_now()},
        "configuration": {
            "hash": config.get("_config_hash"),
            "source_dir": config.get("_config_dir"),
            "revision": config.get("_config_hash"),
        },
    }
    note_ids = {note.id for note in notes}
    snapshot_note_ids = {note.get("id") for note in snapshot["notes"]}
    if note_ids != snapshot_note_ids:
        raise RuntimeError("note completeness invariant failed")
    return snapshot


def freeze_snapshot(
    repository, evidence: EvidenceManager, snapshot: dict[str, Any]
) -> tuple[str, str]:
    run_id = snapshot["run"]["run_id"]
    record = evidence.write_final_json(run_id, "review_snapshot.json", snapshot)
    repository.add_evidence(record)
    repository.set_snapshot_path(run_id, record.path)
    return record.path, record.checksum


def finalize_run(
    repository,
    evidence: EvidenceManager,
    config: dict[str, Any],
    run_id: str,
    reviewer: str,
    decision: ReviewDecision,
    *,
    fail_pdf: bool = False,
) -> dict[str, Any]:
    repository.require_review_ready(run_id)
    enforce_finalization_readiness(repository, config, run_id, decision)
    snapshot = build_snapshot(repository, run_id, config, reviewer, decision)
    snapshot_path, _snapshot_checksum = freeze_snapshot(repository, evidence, snapshot)
    if fail_pdf:
        raise RuntimeError(f"PDF generation failed after snapshot freeze: {snapshot_path}")
    pdf_rel = f"runs/{run_id}/final/weekend-report-{run_id}.pdf"
    pdf_path, checksum = render_pdf_under(evidence.root, snapshot, pdf_rel)
    state = RunState.APPROVED if decision == ReviewDecision.APPROVE else RunState.REJECTED
    repository.set_final_pdf(
        run_id,
        state=state,
        reviewer=reviewer,
        decision=decision.value,
        pdf_path=Path(pdf_path).relative_to(evidence.root).as_posix(),
        checksum=checksum,
    )
    return snapshot
