"""Paginated operational layout, rendered exclusively from the frozen snapshot."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

INK = colors.HexColor("#17313d")
TEAL = colors.HexColor("#087f8c")
PALE = colors.HexColor("#edf3f6")
STATUS_COLORS = {
    "PASS": "#19734b",
    "WARNING": "#956000",
    "FAIL": "#b03428",
    "ERROR": "#a22929",
    "MANUAL_REVIEW": "#6851a6",
    "SKIPPED": "#677681",
    "APPROVE": "#19734b",
    "REJECT": "#b03428",
}
WIDTH = 504


def write_report(snapshot: dict[str, Any], path: Path) -> None:
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            "ReportBody",
            fontName="Helvetica",
            fontSize=9,
            leading=13,
            textColor=INK,
            spaceAfter=6,
            splitLongWords=True,
        )
    )
    styles.add(
        ParagraphStyle("Cell", parent=styles["ReportBody"], fontSize=8, leading=11, spaceAfter=0)
    )
    styles.add(
        ParagraphStyle(
            "Section",
            fontSize=16,
            leading=21,
            textColor=TEAL,
            spaceBefore=14,
            spaceAfter=10,
            keepWithNext=True,
        )
    )
    styles.add(ParagraphStyle("ReportTitle", fontSize=27, leading=33, textColor=INK, spaceAfter=16))

    def paragraph(value: Any, style: str = "ReportBody") -> Paragraph:
        return Paragraph(
            escape(str(value if value is not None else "Unavailable")).replace("\n", "<br/>"),
            styles[style],
        )

    def status(value: str) -> Paragraph:
        color = STATUS_COLORS.get(value, "#677681")
        return Paragraph(f'<font color="{color}"><b>{escape(value)}</b></font>', styles["Cell"])

    def table(headers: list[str], rows: list[list[Any]], widths: list[float]) -> Table:
        data = [[paragraph(value, "Cell") for value in headers]]
        data += [
            [value if isinstance(value, Paragraph) else paragraph(value, "Cell") for value in row]
            for row in rows
        ]
        result = Table(
            data, colWidths=widths, repeatRows=1, hAlign="LEFT", splitByRow=1, splitInRow=1
        )
        result.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), PALE),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                    ("LINEBELOW", (0, 0), (-1, 0), 0.7, TEAL),
                    ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor("#dce4e8")),
                ]
            )
        )
        return result

    story: list[Any] = []
    run, review = snapshot.get("run", {}), snapshot.get("review", {})
    results, notes = snapshot.get("results", []), snapshot.get("notes", [])
    counts = Counter(item.get("status") for item in results)
    sites = [site.get("id") for site in snapshot.get("sites", [])] or [
        site.get("site") for site in snapshot.get("site_summaries", [])
    ]
    story += [
        paragraph("WEEKEND OPERATIONS / FINAL REPORT", "Cell"),
        Spacer(1, 12),
        paragraph("Weekend Report", "ReportTitle"),
        paragraph(run.get("run_id")),
        table(
            ["Execution", "Human review"],
            [
                [
                    f"Version: {run.get('application_version')}\n"
                    f"Started: {run.get('started_at') or run.get('created_at')}\n"
                    f"Finished: {run.get('finished_at') or 'Not recorded'}\n"
                    f"Sites: {', '.join(sites)}",
                    f"Reviewer: {review.get('reviewer')}\nDecision: {review.get('decision')}\n"
                    f"Reviewed: {review.get('confirmed_at')}",
                ]
            ],
            [252, 252],
        ),
        Spacer(1, 12),
    ]
    story += [
        paragraph(
            f"Automation status: {snapshot.get('overall_status') or run.get('automation_status')}"
        ),
        paragraph(f"Cross-site parity: {snapshot.get('parity_status', 'See module findings')}"),
        table(
            ["PASS", "WARNING", "FAIL", "ERROR"],
            [[counts[value] for value in ("PASS", "WARNING", "FAIL", "ERROR")]],
            [126] * 4,
        ),
        paragraph("Key findings", "Section"),
    ]
    attention = [
        item
        for item in results
        if item.get("status") in {"FAIL", "ERROR", "WARNING", "MANUAL_REVIEW"}
    ]
    for item in attention[:4]:
        story.append(
            paragraph(
                f"{item.get('status')} / {item.get('module')} / "
                f"{str(item.get('target') or item.get('site') or 'Module')[:85]}: "
                f"{str(item.get('message') or '')[:150]}"
            )
        )
    if not attention:
        story.append(paragraph("No warning, failure or error findings were recorded."))
    elif len(attention) > 4:
        story.append(
            paragraph(f"{len(attention) - 4} additional findings appear in the detailed section.")
        )
    if review.get("rejection_reason"):
        story.append(paragraph("Rejection reason is retained in Final Review below."))
    story += [PageBreak(), paragraph("Module Summary", "Section")]
    groups: dict[tuple[str, str], Counter] = defaultdict(Counter)
    for item in results:
        groups[(item["module"], item.get("site") or "All sites")][item["status"]] += 1
    rows = []
    for (module, site), tally in sorted(groups.items()):
        review_state = (
            "Needs attention"
            if any(tally[value] for value in ("FAIL", "ERROR", "MANUAL_REVIEW", "WARNING"))
            else "Complete"
        )
        rows.append(
            [
                module,
                site,
                *[tally[value] for value in ("PASS", "WARNING", "FAIL", "ERROR")],
                review_state,
            ]
        )
    story.append(
        table(
            ["Module", "Site", "PASS", "WARN", "FAIL", "ERROR", "Review status"],
            rows,
            [95, 90, 43, 43, 43, 47, 143],
        )
    )
    story += [paragraph("Detailed Findings", "Section")]
    note_by_result = {
        note.get("result_id"): note.get("note") for note in notes if note.get("scope") == "RESULT"
    }
    if not attention:
        story.append(paragraph("No findings require attention. Complete check inventory follows."))
    for item in attention:
        story.append(
            KeepTogether(
                [
                    Spacer(1, 10),
                    paragraph(f"{item.get('module')} / {item.get('site') or 'All sites'}", "Cell"),
                    paragraph(item.get("target") or item.get("check_id")),
                ]
            )
        )
        story.append(
            table(
                ["Finding", "Details"],
                [
                    ["Check", item.get("check_id")],
                    ["Status", status(item["status"])],
                    ["Expected", display_value(item.get("expected"))],
                    ["Actual", display_value(item.get("actual"))],
                    ["Description", item.get("message")],
                    ["Reviewer note", note_by_result.get(item.get("id")) or "No result note"],
                    [
                        "Evidence",
                        "\n".join(item.get("evidence") or []) or "See retained run evidence",
                    ],
                ],
                [90, WIDTH - 90],
            )
        )
    story += [PageBreak(), paragraph("Complete Check Inventory", "Section")]
    story.append(
        table(
            ["Module / Site", "Target / Check", "Status"],
            [
                [
                    f"{item.get('module')} / {item.get('site') or 'All sites'}",
                    f"{item.get('target') or ''}\n{item.get('check_id')}",
                    status(item["status"]),
                ]
                for item in results
            ],
            [117, 302, 85],
        )
    )
    story.append(paragraph("Manual Database Synchronization Check", "Section"))
    db = snapshot.get("manual_db_review")
    if db:
        for label, key in (
            ("Script path", "script_path"),
            ("Execution method", "execution_method"),
            ("Result", "result"),
            ("Timestamp", "reviewed_at"),
            ("Comment", "comment"),
        ):
            story.append(paragraph(f"{label}: {db.get(key)}"))
    else:
        story.append(paragraph("No manual database review recorded."))
    story.append(paragraph("Reviewer Notes", "Section"))
    for note in notes:
        target = (
            note.get("module") or note.get("dashboard_id") or note.get("result_id") or "general"
        )
        story.append(
            paragraph(
                f"{note.get('scope')} / {target}: {note.get('note') or '(acknowledgment only)'}"
            )
        )
    story.append(paragraph("Required Acknowledgments / Splunk", "Section"))
    dashboard_notes = {
        note.get("dashboard_id"): note for note in notes if note.get("scope") == "SPLUNK_DASHBOARD"
    }
    for dashboard in snapshot.get("splunk_dashboards", []):
        note = dashboard_notes.get(dashboard.get("id"), {})
        story.append(
            paragraph(
                f"{dashboard.get('display_name')}: reviewed={note.get('reviewed', False)}; "
                f"required={dashboard.get('required_review')}\n{dashboard.get('url')}"
            )
        )
    story.append(paragraph("Evidence References", "Section"))
    story.append(
        paragraph(
            "Raw responses are retained separately. "
            "Use the reference and SHA-256 checksum to verify the evidence."
        )
    )
    for record in snapshot.get("evidence", []):
        story.append(
            paragraph(
                f"{record.get('module')} / {record.get('site') or 'All sites'}: "
                f"{record.get('path')}\nSHA-256: {record.get('checksum')}",
                "Cell",
            )
        )
        story.append(Spacer(1, 6))
    story += [
        paragraph("Final Review / Reviewer Confirmation", "Section"),
        paragraph(f"Reviewer: {review.get('reviewer')}"),
        paragraph(f"Decision: {review.get('decision')}"),
        paragraph(f"Confirmed at: {review.get('confirmed_at')}"),
        paragraph(f"Rejection reason: {review.get('rejection_reason') or 'Not applicable'}"),
        paragraph(f"build_id: {run.get('build_id')}", "Cell"),
        paragraph(f"configuration_hash: {snapshot.get('configuration', {}).get('hash')}", "Cell"),
    ]

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(TEAL)
        canvas.line(54, 47, 558, 47)
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(INK)
        canvas.drawString(54, 33, str(run.get("run_id", "Weekend Report"))[:65])
        canvas.drawRightString(558, 33, f"Page {doc.page}")
        canvas.restoreState()

    temporary = path.with_suffix(".pdf.tmp")
    doc = SimpleDocTemplate(
        str(temporary),
        pagesize=(612, 792),
        leftMargin=54,
        rightMargin=54,
        topMargin=48,
        bottomMargin=64,
        title="Weekend Report",
        pageCompression=0,
    )
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    temporary.replace(path)


def display_value(value: Any, limit: int = 1800) -> str:
    if value is None:
        return "Unavailable"
    if isinstance(value, dict):
        value = {
            key: item
            for key, item in value.items()
            if key not in {"raw", "raw_api", "entries", "task_states"}
        }
    text = (
        json.dumps(value, ensure_ascii=False, default=str)
        if isinstance(value, dict | list)
        else str(value)
    )
    return text if len(text) <= limit else text[:limit] + " ... [full value in retained evidence]"
