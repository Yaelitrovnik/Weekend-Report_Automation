"""Generate synthetic PDF samples for visual QA; never contacts infrastructure."""

from __future__ import annotations

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.reporting.final_pdf import render_final_pdf


def sample(name: str) -> dict:
    multi = name in {"many-errors", "long-text"}
    sites = [{"id": "site1"}, {"id": "site2"}] if multi else [{"id": "site1"}]
    results = []
    for index in range(246 if multi else 30):
        status = "ERROR" if name == "many-errors" and index < 12 else "PASS"
        if name == "long-text" and index == 0:
            status = "ERROR"
        results.append(
            {
                "id": index + 1,
                "module": ["portainer", "doctor", "rabbitmq", "infrastructure"][index % 4],
                "site": sites[index % len(sites)]["id"],
                "check_id": f"service.health.{index}",
                "target": "recording-gateway-" * (18 if name == "long-text" and index == 0 else 1),
                "status": status,
                "expected": {"desired_replicas": 2},
                "actual": {"running_replicas": 1 if status == "ERROR" else 2},
                "message": (
                    "Long diagnostic message for pagination and wrapping. " * 140
                    + "END OF LONG MESSAGE"
                )
                if name == "long-text" and index == 0
                else (
                    "Replica measurement unavailable"
                    if status == "ERROR"
                    else "Healthy measurement verified"
                ),
                "evidence": [
                    f"runs/WR-QA-153/{sites[index % len(sites)]['id']}/module/"
                    f"result-{index + 1}.json"
                ],
            }
        )
    return {
        "snapshot_version": 3,
        "created_at": "2026-11-08T15:00:00Z",
        "run": {
            "run_id": "WR-QA-153-" + name,
            "application_version": "v1.5.3",
            "build_id": "FIXTURE-QA",
            "state": "REJECTED" if name != "all-pass" else "APPROVED",
            "started_at": "2026-11-08T15:00:00Z",
            "finished_at": "2026-11-08T15:00:16Z",
        },
        "review": {
            "reviewer": "QA Operator",
            "confirmed_at": "2026-11-08T15:10:00Z",
            "decision": "APPROVE" if name == "all-pass" else "REJECT",
            "rejection_reason": None
            if name == "all-pass"
            else "Rejected pending investigation of operational findings.",
        },
        "sites": sites,
        "parity_status": "APPLICABLE" if multi else "NOT_APPLICABLE",
        "overall_status": "ERROR" if multi else "PASS",
        "results": results,
        "notes": [
            {
                "id": 1,
                "scope": "GENERAL",
                "note": "The reviewer verified the retained evidence and recorded the decision.",
            },
            {
                "id": 2,
                "scope": "SPLUNK_DASHBOARD",
                "dashboard_id": "system_health",
                "reviewed": True,
                "note": "Dashboard reviewed; no new alerts.",
            },
        ],
        "splunk_dashboards": [
            {
                "id": "system_health",
                "display_name": "System Health",
                "url": "https://example.invalid/dashboard",
                "required_review": True,
            }
        ],
        "manual_db_review": {
            "script_path": "C:/Scripts/Test-Databases.ps1",
            "execution_method": "Performed locally by reviewer",
            "result": "PASS",
            "reviewed_at": "2026-11-08T15:05:00Z",
            "comment": (
                "Execution complete. Replication PASS. Cleanup verified. "
                "Evidence retained locally."
            ),
        },
        "configuration": {"hash": "fixture-config-hash"},
        "evidence": [
            {
                "module": "portainer",
                "site": "site1",
                "path": "runs/WR-QA-153/site1/portainer/raw-collector.json",
                "checksum": "a" * 64,
            }
        ],
    }


def main() -> None:
    directory = Path("output/pdf")
    directory.mkdir(parents=True, exist_ok=True)
    for name in ("all-pass", "many-errors", "long-text", "single-site-reject"):
        render_final_pdf(copy.deepcopy(sample(name)), directory / f"v1.5.3-{name}.pdf")
        print(directory / f"v1.5.3-{name}.pdf")


if __name__ == "__main__":
    main()
