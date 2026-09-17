from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.api.dependencies import get_config, get_repository
from app.domain import (
    MODULES,
    STATUS_STRENGTH,
    CheckResult,
    CheckStatus,
    NoteScope,
    RunState,
    to_jsonable,
)
from app.orchestrator.aggregation import (
    aggregate_status,
)
from app.orchestrator.aggregation import (
    module_summaries as aggregate_module_summaries,
)
from app.orchestrator.aggregation import (
    site_summaries as aggregate_site_summaries,
)
from app.review.manual_db import (
    build_manual_db_file_uri,
    build_manual_db_powershell_command,
)
from app.review.notes import (
    general_notes_enabled,
    notes_by_key,
)
from app.security import csrf_token_for_template

router = APIRouter()
templates = Jinja2Templates(directory="app/web/templates")
RepoDep = Annotated[Any, Depends(get_repository)]
ConfigDep = Annotated[dict[str, Any], Depends(get_config)]

ATTENTION_STATUSES = {
    CheckStatus.WARNING.value,
    CheckStatus.FAIL.value,
    CheckStatus.ERROR.value,
    CheckStatus.MANUAL_REVIEW.value,
}
STATUS_ORDER = [
    CheckStatus.PASS,
    CheckStatus.WARNING,
    CheckStatus.FAIL,
    CheckStatus.ERROR,
    CheckStatus.MANUAL_REVIEW,
    CheckStatus.SKIPPED,
]
MODULE_NAV = [
    {"id": "overview", "label": "Overview"},
    {"id": "portainer", "label": "Portainer"},
    {"id": "doctor", "label": "DOCTOR"},
    {"id": "rabbitmq", "label": "RabbitMQ"},
    {"id": "recording", "label": "Recording"},
    {"id": "infrastructure", "label": "Infrastructure"},
    {"id": "splunk", "label": "Splunk"},
    {"id": "review", "label": "Review"},
]
MODULE_LABELS = {
    "doctor": "DOCTOR",
    "infrastructure": "Infrastructure",
    "portainer": "Portainer",
    "rabbitmq": "RabbitMQ",
    "recording": "Recording",
    "site_parity": "Site Parity",
    "splunk": "Splunk",
}
RABBITMQ_SECTION_RULES = [
    ("Collection", ("rabbitmq.collection",)),
    ("Queues", ("rabbitmq.queue.",)),
    ("Node Resources", ("rabbitmq.node.",)),
]
RECORDING_STEPS = [
    (
        "device_selection",
        "Selected existing non-recording device",
        ("recording.device_selection",),
    ),
    (
        "pre_start_verification",
        "Verified same device not recording before START",
        ("recording.pre_start_verification",),
    ),
    ("baseline", "Four runtime baselines", ("recording.baseline.",)),
    ("start_action", "Start recording on same device", ("recording.start_action",)),
    ("after_start", "All four observations N+1/M+1", ("recording.after_start.",)),
    ("stop_action", "Stop recording on same device", ("recording.stop_action",)),
    ("after_stop", "All four observations restored", ("recording.after_stop.",)),
    ("cleanup", "Cleanup", ("recording.cleanup",)),
]
INFRASTRUCTURE_SECTIONS = [
    ("Reachability", ("infrastructure.ssh.reachable",)),
    ("Filesystem Usage", ("infrastructure.filesystem.",)),
    ("Chrony/NTP", ("infrastructure.chrony.",)),
]


def _review_context(
    request: Request,
    repo,
    config: dict[str, Any],
    run_id: str,
    module: str | None = None,
) -> dict[str, Any]:
    run = repo.get_run(run_id)
    notes = repo.list_notes(run_id)
    note_lookup = notes_by_key(notes)
    evidence = repo.list_evidence(run_id)
    evidence_by_result: dict[int, list[Any]] = {}
    evidence_by_module: dict[str, list[Any]] = {}
    for record in evidence:
        evidence_by_module.setdefault(record.module, []).append(record)
        if record.result_id is not None:
            evidence_by_result.setdefault(record.result_id, []).append(record)

    all_results = repo.list_results(run_id)
    page_results = repo.list_results(run_id, module) if module is not None else all_results
    dashboards = _dashboard_config(config)

    manual_db_config = config.get("manual_db_check", {})
    manual_db_review = repo.get_manual_db_review(run_id)
    manual_db_enabled = (
        isinstance(manual_db_config, dict)
        and manual_db_config.get("enabled") is True
    )
    manual_db_context: dict[str, Any] = {
        "enabled": manual_db_enabled,
        "required": False,
        "display_name": "",
        "script_path": "",
        "powershell_command": "",
        "file_uri": "",
    }
    if manual_db_enabled:
        script_path = str(
            manual_db_config.get("script_path", "")
        )
        manual_db_context.update(
            {
                "required": (
                    manual_db_config.get("required") is True
                ),
                "display_name": str(
                    manual_db_config.get(
                        "display_name",
                        "Database Synchronization Check",
                    )
                ),
                "script_path": script_path,
                "powershell_command": (
                    build_manual_db_powershell_command(
                        script_path
                    )
                ),
                "file_uri": build_manual_db_file_uri(
                    script_path
                ),
            }
        )

    context: dict[str, Any] = {
        "request": request,
        "run": run,
        "notes": notes,
        "notes_by_key": note_lookup,
        "csrf_token": csrf_token_for_template(),
        "evidence": evidence,
        "evidence_by_result": evidence_by_result,
        "evidence_by_module": evidence_by_module,
        "general_notes_enabled": general_notes_enabled(config),
        "dashboards": dashboards,
        "module_nav": MODULE_NAV,
        "module_label": _module_label,
        "recovery": config.get("rules", {}).get("recovery", {}),
        "manual_db_check": manual_db_context,
        "manual_db_review": (
            to_jsonable(manual_db_review)
            if manual_db_review is not None
            else None
        ),
        "manual_db_editable": (run.state == RunState.REVIEW_READY),
    }
    if module is not None:
        context["module"] = module
    context["results"] = page_results
    context.update(
        _presentation_context(
            run=run,
            all_results=all_results,
            page_results=page_results,
            config=config,
            evidence_by_result=evidence_by_result,
            notes=note_lookup,
            note_records=notes,
            dashboards=dashboards,
            active_module=module,
        )
    )
    return context


def _presentation_context(
    *,
    run: Any,
    all_results: list[CheckResult],
    page_results: list[CheckResult],
    config: dict[str, Any],
    evidence_by_result: dict[int, list[Any]],
    notes: dict[str, str],
    note_records: list[Any],
    dashboards: list[dict[str, Any]],
    active_module: str | None,
) -> dict[str, Any]:
    presented_all = [_present_result(result, evidence_by_result, notes) for result in all_results]
    presented_page = [_present_result(result, evidence_by_result, notes) for result in page_results]
    module_panels = _module_panels(all_results, evidence_by_result, notes, config)
    module_summary_items = _module_summary_items(all_results, config)
    site_summary_items = _site_summary_items(all_results, config)
    overall = _status_name(
        run.automation_status
        if getattr(run, "automation_status", None) is not None
        else aggregate_status(all_results, config)
    )
    attention_results = [
        result for result in presented_all if result["status"] in ATTENTION_STATUSES
    ]
    open_all = config.get("splunk_dashboards", {}).get("open_all", {})
    include_optional = (
        open_all.get("include_optional", False) if isinstance(open_all, dict) else False
    )
    return {
        "active_page": active_module or "overview",
        "overall_status": overall,
        "status_counts": _status_counts(all_results),
        "site_summaries": site_summary_items,
        "module_summaries": module_summary_items,
        "module_panels": module_panels,
        "attention_results": attention_results,
        "page_results": presented_page,
        "all_presented_results": presented_all,
        "portainer_services": _portainer_services(
            [result for result in presented_page if result["module"] == "portainer"]
        ),
        "doctor_services": _doctor_services(
            [result for result in presented_page if result["module"] == "doctor"]
        ),
        "rabbitmq_sections": _rabbitmq_sections(
            [result for result in presented_page if result["module"] == "rabbitmq"]
        ),
        "recording_workflows": _recording_workflows(
            [result for result in presented_page if result["module"] == "recording"]
        ),
        "infrastructure_servers": _infrastructure_servers(
            [result for result in presented_page if result["module"] == "infrastructure"]
        ),
        "dashboard_cards": _dashboard_cards(
            dashboards,
            notes,
            note_records,
            include_optional,
        ),
    }


def _dashboard_config(config: dict[str, Any]) -> list[dict[str, Any]]:
    dashboards = config.get("splunk_dashboards", {}).get("dashboards", [])
    if not isinstance(dashboards, list):
        return []
    cleaned = [dashboard for dashboard in dashboards if isinstance(dashboard, dict)]
    return sorted(cleaned, key=lambda item: int(item.get("order") or 0))


def _module_panels(
    results: list[CheckResult],
    evidence_by_result: dict[int, list[Any]],
    notes: dict[str, str],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    presented_by_module: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        presented_by_module[result.module].append(
            _present_result(result, evidence_by_result, notes)
        )
    summary_by_module = {item["module"]: item for item in _module_summary_items(results, config)}
    ordered_modules = _ordered_modules(results, include_review_modules=True)
    panels = []
    for module in ordered_modules:
        module_results = presented_by_module.get(module, [])
        panels.append(
            {
                "module": module,
                "label": _module_label(module),
                "status": summary_by_module.get(
                    module,
                    {"status": CheckStatus.SKIPPED.value},
                )["status"],
                "result_count": len(module_results),
                "results": module_results,
                "note": notes.get(f"module:{module}", ""),
            }
        )
    return panels


def _module_summary_items(
    results: list[CheckResult],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    summaries = {item.module: item for item in aggregate_module_summaries(results, config)}
    items = []
    for module in _ordered_modules(results, include_review_modules=False):
        summary = summaries.get(module)
        items.append(
            {
                "module": module,
                "label": _module_label(module),
                "status": _status_name(summary.status if summary else CheckStatus.SKIPPED),
                "result_count": summary.result_count if summary else 0,
                "href_module": module,
            }
        )
    return items


def _site_summary_items(
    results: list[CheckResult],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    summaries = {item.site: item for item in aggregate_site_summaries(results, config)}
    sites_config = config.get("servers", {}).get("sites", {})
    site_order = list(sites_config.keys()) if isinstance(sites_config, dict) else []
    for result in results:
        if result.site and result.site not in site_order:
            site_order.append(result.site)
    return [
        {
            "site": site,
            "label": _site_label(site),
            "status": _status_name(
                summaries[site].status if site in summaries else CheckStatus.SKIPPED
            ),
            "result_count": summaries[site].result_count if site in summaries else 0,
        }
        for site in site_order
    ]


def _status_counts(results: list[CheckResult]) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter(_status_name(result.status) for result in results)
    return [{"status": status.value, "count": counts[status.value]} for status in STATUS_ORDER]


def _ordered_modules(
    results: list[CheckResult],
    *,
    include_review_modules: bool,
) -> list[str]:
    nav_modules = [item["id"] for item in MODULE_NAV if item["id"] not in {"overview", "review"}]
    if not include_review_modules:
        nav_modules = [module for module in nav_modules if module != "splunk"]
    configured = [module for module in MODULES if module not in nav_modules]
    detected = sorted({result.module for result in results})
    ordered = []
    for module in [*nav_modules, *configured, *detected]:
        if module not in ordered:
            ordered.append(module)
    if include_review_modules and "site_parity" in detected and "site_parity" not in ordered:
        ordered.append("site_parity")
    return ordered


def _present_result(
    result: CheckResult,
    evidence_by_result: dict[int, list[Any]],
    notes: dict[str, str],
) -> dict[str, Any]:
    result_id = int(result.id) if result.id is not None else None
    return {
        "id": result_id,
        "module": result.module,
        "module_label": _module_label(result.module),
        "check_id": result.check_id,
        "check_label": _check_label(result.check_id),
        "site": result.site,
        "site_label": _site_label(result.site),
        "target": result.target or "",
        "status": _status_name(result.status),
        "message": result.message,
        "expected_summary": _summarize_value(result.expected),
        "actual_summary": _summarize_value(result.actual),
        "expected_pretty": _pretty_value(result.expected),
        "actual_pretty": _pretty_value(result.actual),
        "metadata_pretty": _pretty_value(result.metadata),
        "started_at": result.started_at,
        "finished_at": result.finished_at,
        "evidence": evidence_by_result.get(result_id, []) if result_id is not None else [],
        "note": notes.get(f"result:{result_id}", "") if result_id is not None else "",
        "expected_value": to_jsonable(result.expected),
        "actual_value": to_jsonable(result.actual),
    }


def _portainer_services(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str | None, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for result in results:
        service = result["target"] or result["check_label"]
        grouped[service][result["site"]].append(result)
    services = []
    for service, by_site in sorted(grouped.items()):
        site_rows = []
        for site in _site_order(by_site.keys()):
            checks = by_site.get(site, [])
            site_rows.append(
                {
                    "site": site,
                    "label": _site_label(site),
                    "status": _aggregate_presented(checks),
                    "desired": _portainer_metric(checks, "desired_replicas"),
                    "running": _portainer_metric(checks, "running_replicas"),
                    "healthy": _portainer_metric(checks, "healthy_replicas"),
                    "image": _portainer_metric(checks, "image"),
                    "service_state": _portainer_metric(checks, "service_state"),
                    "checks": checks,
                }
            )
        services.append(
            {
                "service": service,
                "status": _aggregate_presented(
                    [check for checks in by_site.values() for check in checks]
                ),
                "site_rows": site_rows,
                "parity_status": _portainer_parity_status(site_rows),
            }
        )
    return services


def _portainer_metric(results: list[dict[str, Any]], metric: str) -> str:
    for result in results:
        expected = result.get("expected_value")
        actual = result.get("actual_value")
        if isinstance(actual, dict) and metric in actual:
            return str(actual.get(metric))
        if (
            isinstance(expected, dict)
            and metric in expected
            and result["status"] == CheckStatus.ERROR.value
        ):
            return "not captured"
    return "not captured"


def _portainer_parity_status(site_rows: list[dict[str, Any]]) -> str:
    statuses = [row["status"] for row in site_rows]
    if len(site_rows) < 2:
        return CheckStatus.MANUAL_REVIEW.value
    if all(status == CheckStatus.PASS.value for status in statuses):
        return CheckStatus.PASS.value
    return _aggregate_status_names(statuses)


def _doctor_services(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str | None, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for result in results:
        if result["check_id"] != "doctor.service.health":
            continue
        grouped[result["target"] or "service"][result["site"]].append(result)
    services = []
    for service, by_site in sorted(grouped.items()):
        site_rows = []
        for site in _site_order(by_site.keys()):
            checks = by_site.get(site, [])
            site_rows.append(
                {
                    "site": site,
                    "label": _site_label(site),
                    "status": _aggregate_presented(checks),
                    "reason": _doctor_reason(checks),
                    "checks": checks,
                }
            )
        services.append(
            {
                "service": service,
                "status": _aggregate_presented(
                    [check for checks in by_site.values() for check in checks]
                ),
                "site_rows": site_rows,
            }
        )
    return services


def _doctor_reason(results: list[dict[str, Any]]) -> str:
    for result in results:
        actual = result.get("actual_value")
        if isinstance(actual, dict):
            for key in ("reason", "message", "error"):
                if actual.get(key):
                    return str(actual[key])
        if result["status"] != CheckStatus.PASS.value:
            return result["message"]
    return "Healthy"


def _rabbitmq_sections(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    assigned: set[int | None] = set()
    sections = []
    for title, prefixes in RABBITMQ_SECTION_RULES:
        section_results = [result for result in results if result["check_id"].startswith(prefixes)]
        assigned.update(result["id"] for result in section_results)
        sections.append(
            {
                "title": title,
                "status": _aggregate_presented(section_results),
                "results": section_results,
                "problems": [
                    result for result in section_results if result["status"] in ATTENTION_STATUSES
                ],
            }
        )
    other = [result for result in results if result["id"] not in assigned]
    if other:
        sections.append(
            {
                "title": "Other RabbitMQ Checks",
                "status": _aggregate_presented(other),
                "results": other,
                "problems": [result for result in other if result["status"] in ATTENTION_STATUSES],
            }
        )
    return sections


def _recording_workflows(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not results:
        return []
    steps = []
    used_ids: set[int | None] = set()
    for key, label, check_ids in RECORDING_STEPS:
        step_results = [
            result
            for result in results
            if any(result["check_id"].startswith(check_id) for check_id in check_ids)
        ]
        used_ids.update(result["id"] for result in step_results)
        steps.append(
            {
                "key": key,
                "label": label,
                "status": _aggregate_presented(step_results),
                "results": step_results,
            }
        )
    remaining = [result for result in results if result["id"] not in used_ids]
    return [
        {
            "site": None,
            "label": "Manager-controlled workflow",
            "status": _aggregate_presented(results),
            "steps": steps,
            "remaining": remaining,
        }
    ]


def _infrastructure_servers(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_server: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        server = result["target"] or result["site"] or "server"
        by_server[server].append(result)
    servers = []
    for server, server_results in sorted(by_server.items()):
        sections = []
        assigned: set[int | None] = set()
        for title, prefixes in INFRASTRUCTURE_SECTIONS:
            section_results = [
                result for result in server_results if result["check_id"].startswith(prefixes)
            ]
            assigned.update(result["id"] for result in section_results)
            sections.append(
                {
                    "title": title,
                    "status": _aggregate_presented(section_results),
                    "results": section_results,
                }
            )
        remaining = [result for result in server_results if result["id"] not in assigned]
        if remaining:
            sections.append(
                {
                    "title": "Other Checks",
                    "status": _aggregate_presented(remaining),
                    "results": remaining,
                }
            )
        servers.append(
            {
                "server": server,
                "status": _aggregate_presented(server_results),
                "sections": sections,
            }
        )
    return servers


def _dashboard_cards(
    dashboards: list[dict[str, Any]],
    notes: dict[str, str],
    note_records: list[Any],
    include_optional: bool,
) -> list[dict[str, Any]]:
    review_state = {
        note.dashboard_id: bool(note.reviewed)
        for note in note_records
        if note.scope == NoteScope.SPLUNK_DASHBOARD and note.dashboard_id
    }

    return [
        {
            **dashboard,
            "note": notes.get(f"splunk:{dashboard.get('id')}", ""),
            "reviewed": review_state.get(dashboard.get("id"), False),
            "required_label": (
                "Required review" if dashboard.get("required_review") else "Optional"
            ),
            "include_in_open_all": (bool(dashboard.get("required_review")) or include_optional),
        }
        for dashboard in dashboards
    ]


def _aggregate_presented(results: list[dict[str, Any]]) -> str:
    return _aggregate_status_names([result["status"] for result in results])


def _aggregate_status_names(statuses: list[str]) -> str:
    if not statuses:
        return CheckStatus.SKIPPED.value
    enum_statuses = [_status_enum(status) for status in statuses]
    return max(enum_statuses, key=lambda status: STATUS_STRENGTH[status]).value


def _status_name(value: Any) -> str:
    if value is None:
        return CheckStatus.SKIPPED.value
    return str(getattr(value, "value", value))


def _status_enum(value: Any) -> CheckStatus:
    if isinstance(value, CheckStatus):
        return value
    try:
        return CheckStatus(str(value))
    except ValueError:
        return CheckStatus.ERROR


def _site_order(sites: Any) -> list[str | None]:
    return sorted(sites, key=lambda site: (_site_sort_value(site), str(site or "")))


def _site_sort_value(site: str | None) -> int:
    if site == "site1":
        return 1
    if site == "site2":
        return 2
    if site is None:
        return 99
    return 10


def _site_label(site: str | None) -> str:
    if site == "site1":
        return "Site 1"
    if site == "site2":
        return "Site 2"
    return site or "Global"


def _module_label(module: str) -> str:
    return MODULE_LABELS.get(module, module.replace("_", " ").title())


def _check_label(check_id: str) -> str:
    parts = check_id.split(".")
    if len(parts) > 1:
        parts = parts[1:]
    return " ".join(part.replace("_", " ").title() for part in parts)


def _summarize_value(value: Any) -> str:
    normalized = to_jsonable(value)
    if normalized in (None, ""):
        return "Not captured"
    if isinstance(normalized, bool | int | float):
        return str(normalized)
    if isinstance(normalized, str):
        return _truncate(normalized)
    if isinstance(normalized, list):
        if not normalized:
            return "No items"
        if all(not isinstance(item, dict | list) for item in normalized):
            return _truncate(", ".join(str(item) for item in normalized[:5]))
        return f"{len(normalized)} items"
    if isinstance(normalized, dict):
        preferred = [
            "exists",
            "required",
            "service_name",
            "desired_replicas",
            "running_replicas",
            "healthy_replicas",
            "image",
            "service_state",
            "task_counts",
            "ready",
            "unacked",
            "total",
            "checks_performed",
            "count",
            "expected_count",
            "recording",
            "reviewed",
            "reachable",
            "usable",
            "utilization_percent",
            "offset",
            "source",
            "alarm",
        ]
        pieces = []
        for key in preferred:
            if key in normalized:
                pieces.append(f"{key}: {_short_scalar(normalized[key])}")
        if not pieces:
            for key, item in list(normalized.items())[:4]:
                pieces.append(f"{key}: {_short_scalar(item)}")
        return _truncate("; ".join(pieces))
    return _truncate(str(normalized))


def _short_scalar(value: Any) -> str:
    if isinstance(value, dict):
        return f"{len(value)} fields"
    if isinstance(value, list):
        return f"{len(value)} items"
    return str(value)


def _pretty_value(value: Any) -> str:
    normalized = to_jsonable(value)
    if normalized in (None, ""):
        return ""
    return json.dumps(normalized, indent=2, sort_keys=True, default=str)


def _truncate(value: str, limit: int = 180) -> str:
    return value if len(value) <= limit else f"{value[: limit - 1]}..."


@router.get("/runs/{run_id}", response_class=HTMLResponse)
def run_page(
    run_id: str,
    request: Request,
    repo: RepoDep,
    config: ConfigDep,
):
    return templates.TemplateResponse(
        request,
        "run.html",
        _review_context(request, repo, config, run_id),
    )


@router.get("/runs/{run_id}/review", response_class=HTMLResponse)
def review_page(
    run_id: str,
    request: Request,
    repo: RepoDep,
    config: ConfigDep,
):
    return templates.TemplateResponse(
        request,
        "review.html",
        _review_context(request, repo, config, run_id),
    )


@router.get("/runs/{run_id}/{module}", response_class=HTMLResponse)
def module_page(
    run_id: str,
    module: str,
    request: Request,
    repo: RepoDep,
    config: ConfigDep,
):
    if module == "splunk":
        return templates.TemplateResponse(
            request,
            "splunk.html",
            _review_context(request, repo, config, run_id, module),
        )
    return templates.TemplateResponse(
        request,
        "module.html",
        _review_context(request, repo, config, run_id, module),
    )
