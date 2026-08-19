from __future__ import annotations

import html
from typing import Any


def render_final_html(snapshot: dict[str, Any]) -> str:
    parts = [
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Weekend Report</title></head><body>",
        f"<h1>Weekend Report {html.escape(snapshot['run']['run_id'])}</h1>",
        f"<p>Automation status: {html.escape(str(snapshot['run'].get('automation_status')))}</p>",
        f"<p>Overall status: {html.escape(str(snapshot.get('overall_status')))}</p>",
        f"<p>Reviewer: {html.escape(snapshot['review']['reviewer'])}</p>",
        f"<p>Decision: {html.escape(snapshot['review']['decision'])}</p>",
        "<h2>Site Summaries</h2>",
    ]
    for summary in snapshot.get("site_summaries", []):
        parts.append(
            f"<p>{html.escape(str(summary.get('site')))}: "
            f"{html.escape(str(summary.get('status')))} "
            f"({html.escape(str(summary.get('result_count')))} results)</p>"
        )
    parts.extend(
        [
            "<h2>Module Summaries</h2>",
        ]
    )
    for summary in snapshot.get("module_summaries", []):
        parts.append(
            f"<p>{html.escape(str(summary.get('module')))}: "
            f"{html.escape(str(summary.get('status')))} "
            f"({html.escape(str(summary.get('result_count')))} results)</p>"
        )
    parts.extend(
        [
            "<h2>Parity Summaries</h2>",
        ]
    )
    for summary in snapshot.get("parity_summaries", []):
        parts.append(
            f"<p>{html.escape(str(summary.get('check_id')))}: "
            f"{html.escape(str(summary.get('status')))}</p>"
        )
    parts.extend(
        [
            "<h2>Automated Findings</h2>",
        ]
    )
    for result in snapshot["results"]:
        parts.append(
            "<section>"
            f"<h3>{html.escape(result['module'])} / {html.escape(result['check_id'])}</h3>"
            f"<p>Status: <strong>{html.escape(result['status'])}</strong></p>"
            f"<p>{html.escape(result['message'])}</p>"
            "</section>"
        )
    parts.append("<h2>Reviewer Notes</h2>")
    for note in snapshot["notes"]:
        target = (
            note.get("module") or note.get("dashboard_id") or note.get("result_id") or "general"
        )
        parts.append(
            f"<p>[{html.escape(note['scope'])}] {html.escape(str(target))}: "
            f"{html.escape(note['note'])}</p>"
        )
    parts.append("<h2>Splunk Dashboards</h2>")
    for dash in snapshot.get("splunk_dashboards", []):
        parts.append(f"<p>{html.escape(dash['display_name'])}: {html.escape(dash['url'])}</p>")
    parts.append("</body></html>")
    return "\n".join(parts)
