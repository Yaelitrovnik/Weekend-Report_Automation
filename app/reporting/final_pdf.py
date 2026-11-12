from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.evidence.checksum import sha256_file
from app.evidence.paths import ensure_under
from app.reporting.pdf_layout import write_report


def render_final_pdf(snapshot: dict[str, Any], output_path: str | Path) -> tuple[str, str]:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_report(snapshot, path)
    return str(path), sha256_file(path)


def render_pdf_under(root: Path, snapshot: dict[str, Any], relative_path: str) -> tuple[str, str]:
    path = ensure_under(root, root / relative_path)
    return render_final_pdf(snapshot, path)


def snapshot_to_lines(snapshot: dict[str, Any]) -> list[str]:
    run = snapshot.get("run", {})
    review = snapshot.get("review", {})
    lines = [
        "Weekend Report Final PDF",
        "",
        "Run Metadata",
        f"run_id: {run.get('run_id')}",
        f"state: {run.get('state')}",
        f"automation_status: {run.get('automation_status')}",
        f"overall_status: {snapshot.get('overall_status')}",
        f"created_at: {run.get('created_at')}",
        f"started_at: {run.get('started_at')}",
        f"finished_at: {run.get('finished_at')}",
        f"worker_id: {run.get('worker_id')}",
        f"application_version: {run.get('application_version')}",
        f"build_id: {run.get('build_id')}",
        f"git_commit: {run.get('git_commit')}",
        f"config_version: {run.get('config_version')}",
        "",
        "Configuration Metadata",
        f"configuration_hash: {snapshot.get('configuration', {}).get('hash')}",
        f"configuration_revision: {snapshot.get('configuration', {}).get('revision')}",
        f"config_source_dir: {snapshot.get('configuration', {}).get('source_dir')}",
        f"snapshot_version: {snapshot.get('snapshot_version')}",
        f"snapshot_created_at: {snapshot.get('created_at')}",
    ]
    lines.extend(_summary_lines("Site Summaries", snapshot.get("site_summaries", []), "site"))
    lines.extend(_summary_lines("Module Summaries", snapshot.get("module_summaries", []), "module"))
    lines.extend(_parity_lines(snapshot.get("parity_summaries", [])))
    lines.extend(_result_lines(snapshot.get("results", [])))
    lines.extend(_manual_db_lines(snapshot.get("manual_db_review")))
    lines.extend(_note_lines(snapshot.get("notes", [])))
    lines.extend(_splunk_lines(snapshot.get("splunk_dashboards", []), snapshot.get("notes", [])))
    lines.extend(_evidence_lines(snapshot.get("evidence", [])))
    lines.extend(
        [
            "Reviewer Confirmation",
            f"Reviewer: {review.get('reviewer')}",
            f"Decision: {review.get('decision')}",
            f"Confirmed at: {review.get('confirmed_at')}",
            "",
        ]
    )
    return lines


def _summary_lines(title: str, summaries: list[dict[str, Any]], label: str) -> list[str]:
    lines = [title]
    if not summaries:
        return lines + ["none", ""]
    for item in summaries:
        lines.append(
            f"{label}: {item.get(label)} | status: {item.get('status')} | "
            f"result_count: {item.get('result_count')}"
        )
    lines.append("")
    return lines


def _parity_lines(parity: list[dict[str, Any]]) -> list[str]:
    lines = ["Parity Summaries"]
    if not parity:
        return lines + ["none", ""]
    for item in parity:
        lines.extend(
            [
                f"check_id: {item.get('check_id')} | target: {item.get('target')} | "
                f"status: {item.get('status')}",
                f"message: {item.get('message')}",
                f"expected: {_json(item.get('expected'))}",
                f"actual: {_json(item.get('actual'))}",
                f"evidence: {', '.join(item.get('evidence') or [])}",
                "",
            ]
        )
    return lines


def _result_lines(results: list[dict[str, Any]]) -> list[str]:
    lines = ["Automated Findings"]
    if not results:
        return lines + ["none", ""]
    for result in results:
        lines.extend(
            [
                f"result_id: {result.get('id')} | module: {result.get('module')} | "
                f"site: {result.get('site')} | check_id: {result.get('check_id')}",
                f"target: {result.get('target')} | status: {result.get('status')}",
                f"message: {result.get('message')}",
                f"expected: {_json(result.get('expected'))}",
                f"actual: {_json(result.get('actual'))}",
                f"evidence: {', '.join(result.get('evidence') or [])}",
                "",
            ]
        )
    return lines


def _manual_db_lines(
    review: dict[str, Any] | None,
) -> list[str]:
    lines = ["Manual Database Synchronization Check"]

    if not review:
        return lines + [
            "none",
            "",
        ]

    lines.extend(
        [
            (f"Script path: {review.get('script_path')}"),
            (f"Execution method: {review.get('execution_method')}"),
            f"Result: {review.get('result')}",
            (f"Timestamp: {review.get('reviewed_at')}"),
            f"Comment: {review.get('comment')}",
            "",
        ]
    )

    return lines


def _note_lines(notes: list[dict[str, Any]]) -> list[str]:
    lines = ["Reviewer Notes"]
    if not notes:
        return lines + ["none", ""]
    for note in notes:
        target = (
            note.get("module") or note.get("dashboard_id") or note.get("result_id") or "general"
        )
        lines.extend(
            [
                f"note_id: {note.get('id')} | scope: {note.get('scope')} | target: {target}",
                f"reviewed: {note.get('reviewed')} | updated_at: {note.get('updated_at')}",
                f"note: {note.get('note')}",
                "",
            ]
        )
    return lines


def _splunk_lines(dashboards: list[dict[str, Any]], notes: list[dict[str, Any]]) -> list[str]:
    lines = ["Splunk Dashboard Notes"]
    notes_by_dashboard = {
        note.get("dashboard_id"): note for note in notes if note.get("scope") == "SPLUNK_DASHBOARD"
    }
    if not dashboards:
        return lines + ["none", ""]
    for dashboard in dashboards:
        dashboard_id = dashboard.get("id")
        note = notes_by_dashboard.get(dashboard_id, {})
        lines.extend(
            [
                f"dashboard_id: {dashboard_id} | display_name: {dashboard.get('display_name')}",
                f"url: {dashboard.get('url')}",
                f"required_review: {dashboard.get('required_review')} | "
                f"note_required: {dashboard.get('note_required')} | "
                f"order: {dashboard.get('order')}",
                f"reviewed: {note.get('reviewed', False)}",
                f"note: {note.get('note', '')}",
                "",
            ]
        )
    return lines


def _evidence_lines(evidence: list[dict[str, Any]]) -> list[str]:
    lines = ["Evidence References"]
    if not evidence:
        return lines + ["none", ""]
    for item in evidence:
        lines.extend(
            [
                f"evidence_id: {item.get('id')} | result_id: {item.get('result_id')} | "
                f"module: {item.get('module')} | site: {item.get('site')}",
                f"type: {item.get('evidence_type')} | mime: {item.get('mime_type')}",
                f"path: {item.get('path')}",
                f"checksum: {item.get('checksum')}",
                "",
            ]
        )
    return lines


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)
