from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.domain import ReviewDecision, RunState, to_jsonable
from app.evidence.manager import EvidenceManager
from app.evidence.redaction import sanitize
from app.observability import event
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
    manual_db_review = repository.get_manual_db_review(run_id)
    dashboards = config.get("splunk_dashboards", {}).get("dashboards", [])
    parity_results = [
        result
        for result in results
        if result.module == "site_parity" or result.metadata.get("parity_only") is True
    ]
    manual_db_snapshot: dict[str, Any] | None = None
    if manual_db_review is not None:
        manual_db_snapshot = {
            "id": manual_db_review.id,
            "run_id": manual_db_review.run_id,
            "display_name": manual_db_review.display_name,
            "script_path": manual_db_review.script_path,
            "execution_method": "Performed locally by reviewer",
            "result": manual_db_review.result.value,
            "comment": manual_db_review.comment,
            "reviewed_at": manual_db_review.reviewed_at,
            "final_decision": decision.value,
        }
    snapshot = {
        "snapshot_version": 3,
        "parity_status": "NOT_APPLICABLE"
        if len(config.get("sites", {}).get("sites", [])) == 1
        else "APPLICABLE",
        "sites": config.get("sites", {}).get("sites", []),
        "created_at": iso_now(),
        "run": to_jsonable(run),
        "results": to_jsonable(results),
        "evidence": to_jsonable(evidence),
        "notes": to_jsonable(notes),
        "manual_db_review": manual_db_snapshot,
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
    rejection_reason: str | None = None,
) -> dict[str, Any]:
    if not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer) > 100:
        raise ValueError("Reviewer name is required and must be at most 100 characters")
    if any(char in reviewer for char in ("\r", "\n", "\x00")):
        raise ValueError("Reviewer name contains unsupported characters")
    reviewer = reviewer.strip()
    failure = None
    with repository.finalization_lock, repository.transaction():
        if repository.backend == "postgres":
            repository._execute("SELECT run_id FROM runs WHERE run_id=? FOR UPDATE", (run_id,))
        run = repository.require_review_ready(run_id)
        if run.final_snapshot_path:
            snapshot = json.loads(
                evidence.absolute(run.final_snapshot_path).read_text(encoding="utf-8")
            )
            if (
                snapshot["review"]["decision"] != decision.value
                or snapshot["review"]["reviewer"] != reviewer
            ):
                raise ValueError(
                    "Snapshot is frozen; retry with the original reviewer and decision"
                )
            if rejection_reason is not None and (
                not isinstance(rejection_reason, str)
                or sanitize(rejection_reason.strip())
                != snapshot["review"].get("rejection_reason")
            ):
                raise ValueError("Snapshot is frozen; rejection reason cannot change")
        else:
            enforce_finalization_readiness(repository, config, run_id, decision)
            reason = rejection_reason
            if decision == ReviewDecision.REJECT:
                if reason is None:
                    reason = next(
                        (
                            note.note
                            for note in repository.list_notes(run_id)
                            if note.scope.value == "GENERAL"
                        ),
                        "",
                    )
                if not isinstance(reason, str) or not reason.strip() or len(reason) > 10000:
                    raise ValueError(
                        "REJECT requires a non-empty rejection reason (at most 10000 characters)"
                    )
            else:
                reason = None
            snapshot = build_snapshot(repository, run_id, config, reviewer.strip(), decision)
            snapshot["review"]["rejection_reason"] = sanitize(reason.strip() if reason else None)
            state = RunState.APPROVED if decision == ReviewDecision.APPROVE else RunState.REJECTED
            snapshot["run"].update(
                {
                    "state": state.value,
                    "reviewed_by": reviewer.strip(),
                    "review_decision": decision.value,
                    "reviewed_at": snapshot["review"]["confirmed_at"],
                    "rejection_reason": snapshot["review"]["rejection_reason"],
                }
            )
            snapshot = sanitize(snapshot)
            freeze_snapshot(repository, evidence, snapshot)
        try:
            if fail_pdf:
                raise RuntimeError("PDF generation failed after snapshot freeze")
            pdf_rel = f"runs/{run_id}/final/weekend-report-{run_id}.pdf"
            pdf_path, checksum = render_pdf_under(evidence.root, snapshot, pdf_rel)
        except Exception as exc:
            failure = exc
        else:
            state = RunState.APPROVED if decision == ReviewDecision.APPROVE else RunState.REJECTED
            repository.set_final_pdf(
                run_id,
                state=state,
                reviewer=reviewer.strip(),
                decision=decision.value,
                pdf_path=Path(pdf_path).relative_to(evidence.root).as_posix(),
                checksum=checksum,
                rejection_reason=snapshot["review"].get("rejection_reason"),
                reviewed_at=snapshot["review"]["confirmed_at"],
            )
    if failure is not None:
        event(
            "report_render_error",
            run_id=run_id,
            severity="ERROR",
            error_type=type(failure).__name__,
        )
        raise failure
    event("review_finalized", run_id=run_id, decision=decision.value)
    return snapshot
