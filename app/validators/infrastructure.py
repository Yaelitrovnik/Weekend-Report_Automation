from __future__ import annotations

import re
from typing import Any

from app.domain import CheckResult, CheckStatus
from app.orchestrator.run_context import RunContext
from app.time_utils import iso_now
from app.validators.base import Validator
from app.validators.engine import threshold_status

DF_RE = re.compile(r"^(?P<fs>\S+)\s+\S+\s+\S+\s+\S+\s+(?P<use>\d+)%\s+(?P<mount>\S+)")


def parse_df(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        match = DF_RE.match(line.strip())
        if match:
            rows.append(
                {
                    "filesystem": match.group("fs"),
                    "utilization_percent": int(match.group("use")),
                    "mountpoint": match.group("mount"),
                }
            )
    return rows


class InfrastructureValidator(Validator):
    def validate(
        self, actual: dict[str, Any], config: dict[str, Any], context: RunContext
    ) -> list[CheckResult]:
        started = iso_now()
        if actual.get("error"):
            return [
                _r(
                    context.run_id,
                    "collection",
                    None,
                    "infrastructure",
                    {"source": "approved SSH read-only commands"},
                    actual,
                    CheckStatus.ERROR,
                    actual["error"],
                    started,
                )
            ]
        results: list[CheckResult] = []
        expected_sites = config.get("servers", {}).get("sites", {})
        actual_sites = actual.get("sites", {})
        for site, site_expected in expected_sites.items():
            site_actual = actual_sites.get(site, {})
            server_outputs = site_actual.get("servers", {}) if isinstance(site_actual, dict) else {}
            for server in site_expected.get("servers", []):
                server_id = str(server.get("id") or server.get("hostname") or "server")
                outputs = server_outputs.get(server_id, {})
                if not isinstance(outputs, dict):
                    outputs = {}
                results.extend(
                    _validate_server(context.run_id, site, server_id, server, outputs, started)
                )
        return results


def _validate_server(
    run_id: str,
    site: str,
    server_id: str,
    server: dict[str, Any],
    outputs: dict[str, Any],
    started: str,
) -> list[CheckResult]:
    results: list[CheckResult] = []
    if outputs.get("reachable") is not True:
        results.append(
            _r(
                run_id,
                "ssh.reachable",
                site,
                server_id,
                {"reachable": True},
                {
                    "reachable": False,
                    "error": outputs.get("df_error") or "SSH connection failed",
                },
                CheckStatus.ERROR,
                "server unreachable over SSH",
                started,
            )
        )
        return results

    results.append(
        _r(
            run_id,
            "ssh.reachable",
            site,
            server_id,
            {"reachable": True},
            {"reachable": outputs.get("reachable", True)},
            CheckStatus.PASS,
            "server reachable",
            started,
        )
    )
    for filesystem in server.get("filesystems", []):
        results.append(_filesystem_result(run_id, site, server_id, filesystem, outputs, started))
    for chrony in server.get("chrony", []):
        results.extend(_chrony_results(run_id, site, server_id, chrony, outputs, started))
    return results


def _filesystem_result(
    run_id: str,
    site: str,
    server_id: str,
    filesystem: dict[str, Any],
    outputs: dict[str, Any],
    started: str,
) -> CheckResult:
    expected_path = filesystem.get("path", "/")
    if outputs.get("df_error"):
        return _r(
            run_id,
            "filesystem.utilization",
            site,
            server_id,
            filesystem,
            {"error": outputs.get("df_error")},
            CheckStatus.ERROR,
            "filesystem command failed",
            started,
        )
    rows = parse_df(str(outputs.get("df", "")))
    if not rows:
        return _r(
            run_id,
            "filesystem.utilization",
            site,
            server_id,
            filesystem,
            {"df": outputs.get("df", "")},
            CheckStatus.ERROR,
            "filesystem utilization could not be parsed",
            started,
        )
    by_mount = {row["mountpoint"]: row for row in rows}
    row = by_mount.get(expected_path)
    if not row:
        return _r(
            run_id,
            "filesystem.exists",
            site,
            server_id,
            filesystem,
            {"mountpoints": sorted(by_mount)},
            CheckStatus.FAIL if filesystem.get("required", True) else CheckStatus.SKIPPED,
            f"expected filesystem {expected_path} missing",
            started,
        )
    status = threshold_status(
        float(row["utilization_percent"]),
        float(filesystem["warning_percent"]),
        float(filesystem["critical_percent"]),
    )
    return _r(
        run_id,
        "filesystem.utilization",
        site,
        server_id,
        filesystem,
        row,
        status,
        f"{row['mountpoint']} utilization {row['utilization_percent']}%",
        started,
    )


def _chrony_results(
    run_id: str,
    site: str,
    server_id: str,
    expected: dict[str, Any],
    outputs: dict[str, Any],
    started: str,
) -> list[CheckResult]:
    chrony = outputs.get("chrony")
    if outputs.get("chrony_error") or not isinstance(chrony, dict):
        return [
            _r(
                run_id,
                "chrony.collection",
                site,
                server_id,
                expected,
                {"error": outputs.get("chrony_error"), "chrony": chrony},
                CheckStatus.ERROR,
                "Chrony state could not be collected reliably",
                started,
            )
        ]

    results = []
    timezone = chrony.get("timezone")
    expected_timezone = expected.get("timezone")
    if timezone is None:
        results.append(
            _r(
                run_id,
                "chrony.timezone",
                site,
                server_id,
                {"timezone": expected_timezone},
                chrony,
                CheckStatus.ERROR,
                "timezone state is unavailable",
                started,
            )
        )
    else:
        status = CheckStatus.PASS if timezone == expected_timezone else CheckStatus.FAIL
        results.append(
            _r(
                run_id,
                "chrony.timezone",
                site,
                server_id,
                {"timezone": expected_timezone},
                {"timezone": timezone},
                status,
                "timezone matches expected" if status == CheckStatus.PASS else "timezone mismatch",
                started,
            )
        )

    synchronized = chrony.get("synchronized")
    if not isinstance(synchronized, bool):
        results.append(
            _r(
                run_id,
                "chrony.synchronized",
                site,
                server_id,
                {"synchronized": True},
                chrony,
                CheckStatus.ERROR,
                "chrony synchronization state is unavailable",
                started,
            )
        )
    else:
        status = CheckStatus.PASS if synchronized else CheckStatus.FAIL
        results.append(
            _r(
                run_id,
                "chrony.synchronized",
                site,
                server_id,
                {"synchronized": True},
                {"synchronized": synchronized},
                status,
                "chrony synchronized" if synchronized else "chrony unsynchronized",
                started,
            )
        )

    actual_source = chrony.get("source")
    expected_source = expected.get("source")
    if not isinstance(actual_source, str) or not actual_source:
        results.append(
            _r(
                run_id,
                "chrony.source",
                site,
                server_id,
                {"source": expected_source},
                chrony,
                CheckStatus.ERROR,
                "chrony source is unavailable",
                started,
            )
        )
    else:
        status = CheckStatus.PASS if actual_source == expected_source else CheckStatus.FAIL
        results.append(
            _r(
                run_id,
                "chrony.source",
                site,
                server_id,
                {"source": expected_source},
                {"source": actual_source},
                status,
                "chrony source matches expected"
                if status == CheckStatus.PASS
                else "chrony source mismatch",
                started,
            )
        )

    try:
        offset = abs(float(chrony["offset"]))
    except (KeyError, TypeError, ValueError):
        results.append(
            _r(
                run_id,
                "chrony.offset",
                site,
                server_id,
                expected,
                chrony,
                CheckStatus.ERROR,
                "chrony offset is unavailable",
                started,
            )
        )
    else:
        status = threshold_status(
            offset,
            float(expected["warning_offset"]),
            float(expected["critical_offset"]),
        )
        results.append(
            _r(
                run_id,
                "chrony.offset",
                site,
                server_id,
                expected,
                {**chrony, "absolute_offset": offset},
                status,
                f"absolute offset {offset}",
                started,
            )
        )
    return results


def _r(
    run_id: str,
    check: str,
    site: str | None,
    target: str,
    expected: Any,
    actual: Any,
    status: CheckStatus,
    message: str,
    started: str,
) -> CheckResult:
    return CheckResult(
        run_id,
        "infrastructure",
        f"infrastructure.{check}",
        status,
        message,
        site=site,
        target=target,
        expected=expected,
        actual=actual,
        started_at=started,
        finished_at=iso_now(),
    )
