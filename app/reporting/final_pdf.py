from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.evidence.checksum import sha256_file
from app.evidence.paths import ensure_under

PAGE_WIDTH = 612
PAGE_HEIGHT = 792
LEFT = 46
TOP = 750
LINE_HEIGHT = 12
FONT_SIZE = 9
LINES_PER_PAGE = 56
WRAP_WIDTH = 104


def render_final_pdf(snapshot: dict[str, Any], output_path: str | Path) -> tuple[str, str]:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = snapshot_to_lines(snapshot)
    _write_pdf(path, _paginate(lines))
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
        f"started_by: {run.get('started_by')}",
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
        "",
        "Reviewer Confirmation",
        f"reviewer: {review.get('reviewer')}",
        f"decision: {review.get('decision')}",
        f"confirmed_at: {review.get('confirmed_at')}",
        "",
    ]
    lines.extend(_summary_lines("Site Summaries", snapshot.get("site_summaries", []), "site"))
    lines.extend(_summary_lines("Module Summaries", snapshot.get("module_summaries", []), "module"))
    lines.extend(_parity_lines(snapshot.get("parity_summaries", [])))
    lines.extend(_result_lines(snapshot.get("results", [])))
    lines.extend(_note_lines(snapshot.get("notes", [])))
    lines.extend(_splunk_lines(snapshot.get("splunk_dashboards", []), snapshot.get("notes", [])))
    lines.extend(_evidence_lines(snapshot.get("evidence", [])))
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
                f"author: {note.get('author')} | updated_at: {note.get('updated_at')}",
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


def _paginate(lines: list[str]) -> list[list[str]]:
    wrapped: list[str] = []
    for line in lines:
        if not line:
            wrapped.append("")
            continue
        wrapped.extend(_wrap(line, WRAP_WIDTH))
    if not wrapped:
        wrapped = [""]
    return [
        wrapped[index : index + LINES_PER_PAGE] for index in range(0, len(wrapped), LINES_PER_PAGE)
    ]


def _pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _write_pdf(path: Path, pages: list[list[str]]) -> None:
    page_count = len(pages)
    font_id = 3
    first_page_id = 4
    first_content_id = first_page_id + page_count
    page_ids = list(range(first_page_id, first_page_id + page_count))
    content_ids = list(range(first_content_id, first_content_id + page_count))
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        (
            "<< /Type /Pages /Kids ["
            + " ".join(f"{page_id} 0 R" for page_id in page_ids)
            + f"] /Count {page_count} >>"
        ).encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for _page_id, content_id in zip(page_ids, content_ids, strict=True):
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
                f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>"
            ).encode()
        )
    for page_number, page_lines in enumerate(pages, start=1):
        stream = _page_stream(page_lines, page_number, page_count)
        objects.append(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for idx, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out.extend(f"{idx} 0 obj\n".encode())
        out.extend(obj)
        out.extend(b"\nendobj\n")
    xref = len(out)
    out.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        out.extend(f"{offset:010d} 00000 n \n".encode())
    out.extend(
        f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    path.write_bytes(bytes(out))


def _page_stream(lines: list[str], page_number: int, page_count: int) -> bytes:
    content_lines = [
        "BT",
        f"/F1 {FONT_SIZE} Tf",
        f"{LEFT} {TOP} Td",
        f"{LINE_HEIGHT} TL",
    ]
    for line in lines:
        content_lines.append(f"({_pdf_escape(line)}) Tj")
        content_lines.append("T*")
    content_lines.append(f"(Page {page_number} of {page_count}) Tj")
    content_lines.append("ET")
    return "\n".join(content_lines).encode("latin-1", "replace")


def _wrap(text: str, width: int) -> list[str]:
    if len(text) <= width:
        return [text]
    chunks: list[str] = []
    current = text
    while len(current) > width:
        split_at = current.rfind(" ", 0, width + 1)
        if split_at < max(20, width // 2):
            split_at = width
        chunks.append(current[:split_at])
        current = current[split_at:].lstrip()
    if current:
        chunks.append(current)
    return chunks
