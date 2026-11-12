from __future__ import annotations

import math
import re
from typing import Any

from app.domain import CheckResult, CheckStatus
from app.orchestrator.run_context import RunContext
from app.time_utils import iso_now
from app.validators.base import Validator
from app.validators.engine import threshold_status

DF_RE = re.compile(r"(?P<fs>\S+)\s+\S+\s+\S+\s+\S+\s+(?P<use>\d+)%\s+(?P<mount>/[^\r\n]*)")


def parse_df(text: str, match_policy: str = "mapper") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip() or (match_policy == "root" and line.strip().startswith("Filesystem")):
            continue
        match = DF_RE.fullmatch(line.strip())
        if match is None or (match_policy == "mapper" and "mapper" not in match.group("fs")):
            raise ValueError(f"unparseable mapper filesystem output at line {line_number}")
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
        results.extend(_filesystem_results(run_id, site, server_id, filesystem, outputs, started))
    for chrony in server.get("chrony", []):
        results.extend(_chrony_results(run_id, site, server_id, chrony, outputs, started))
    return results


def _filesystem_results(
    run_id: str,
    site: str,
    server_id: str,
    filesystem: dict[str, Any],
    outputs: dict[str, Any],
    started: str,
) -> list[CheckResult]:
    if outputs.get("df_error"):
        return [
            _r(
                run_id,
                "filesystem.utilization",
                site,
                server_id,
                filesystem,
                {"error": outputs["df_error"], "df": outputs.get("df", "")},
                CheckStatus.ERROR,
                "filesystem command failed",
                started,
            )
        ]
    try:
        raw = outputs.get("df")
        if not isinstance(raw, str):
            raise ValueError("filesystem output is unavailable")
        rows = parse_df(raw, filesystem.get("match", "mapper"))
        if filesystem.get("match") == "root":
            rows = [row for row in rows if row["mountpoint"] == "/"]
    except ValueError as exc:
        return [
            _r(
                run_id,
                "filesystem.utilization",
                site,
                server_id,
                filesystem,
                {"df": outputs.get("df"), "error": str(exc)},
                CheckStatus.ERROR,
                "filesystem utilization could not be parsed",
                started,
            )
        ]
    if not rows:
        return [
            _r(
                run_id,
                "filesystem.exists",
                site,
                server_id,
                filesystem,
                {"filesystems": [], "df": raw},
                CheckStatus.FAIL,
                "No required filesystems were found"
                if filesystem.get("match") == "root"
                else "No mapper filesystems were found",
                started,
            )
        ]
    return [
        _r(
            run_id,
            "filesystem.utilization",
            site,
            server_id,
            filesystem,
            row,
            threshold_status(
                float(row["utilization_percent"]),
                float(filesystem["warning_percent"]),
                float(filesystem["critical_percent"]),
            ),
            f"{row['filesystem']} mounted on {row['mountpoint']}: "
            f"utilization {row['utilization_percent']}%",
            started,
        )
        for row in rows
    ]


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
        status = (
            CheckStatus.PASS
            if timezone in [expected_timezone, *expected.get("timezone_aliases", [])]
            else CheckStatus.FAIL
        )
        results.append(
            _r(
                run_id,
                "chrony.timezone",
                site,
                server_id,
                {"timezone": expected_timezone},
                {"timezone": timezone},
                status,
                "timezone matches expected"
                if status == CheckStatus.PASS
                else "TIMEZONE_MISMATCH: timezone mismatch",
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
                "chrony synchronized"
                if synchronized
                else "NTP_UNSYNCHRONIZED: chrony unsynchronized",
                started,
            )
        )

    actual_source = chrony.get("source")
    expected_source = expected.get("source")
    if chrony.get("local_reference") is True:
        policy = expected.get("local_reference_status", "WARNING")
        results.append(
            _r(
                run_id,
                "chrony.local_reference",
                site,
                server_id,
                {"local_reference_status": policy},
                chrony,
                CheckStatus(policy),
                "NTP_LOCAL_REFERENCE_ACTIVE: tracking uses a local reference",
                started,
            )
        )
    elif chrony.get("source_basis") == "selected_source_only":
        results.append(
            _r(
                run_id,
                "chrony.source",
                site,
                server_id,
                {"source": expected_source},
                chrony,
                CheckStatus.ERROR,
                "NTP_REFERENCE_UNAVAILABLE: selected source alone "
                "does not verify tracked reference",
                started,
            )
        )
    elif not isinstance(actual_source, str) or not actual_source:
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
        status = (
            CheckStatus.PASS
            if actual_source in [expected_source, *expected.get("source_aliases", [])]
            else CheckStatus.FAIL
        )
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
                else "NTP_SOURCE_MISMATCH: chrony source mismatch",
                started,
            )
        )

    candidates = [
        row
        for row in chrony.get("sources", [])
        if row.get("source") in [expected_source, *expected.get("source_aliases", [])]
    ]
    if candidates and all(row.get("reach") == 0 for row in candidates):
        results.append(
            _r(
                run_id,
                "chrony.reachability",
                site,
                server_id,
                {"source": expected_source, "reach": ">0"},
                candidates,
                CheckStatus.FAIL,
                "NTP_SOURCE_UNREACHABLE: source reach register is zero",
                started,
            )
        )
    maximum = expected.get("max_root_distance_seconds")
    if maximum is not None:
        delay, dispersion = chrony.get("root_delay"), chrony.get("root_dispersion")
        actual_distance: dict[str, Any]
        if (
            isinstance(delay, int | float)
            and not isinstance(delay, bool)
            and isinstance(dispersion, int | float)
            and not isinstance(dispersion, bool)
            and math.isfinite(delay)
            and math.isfinite(dispersion)
            and dispersion >= 0
        ):
            distance = abs(float(delay)) / 2 + float(dispersion)
            status = CheckStatus.FAIL if distance > maximum else CheckStatus.PASS
            message = (
                "NTP_EXCESSIVE_UNCERTAINTY: root distance exceeds policy"
                if status == CheckStatus.FAIL
                else "NTP root distance within policy"
            )
            actual_distance = {"root_distance_seconds": distance}
        else:
            status, message = CheckStatus.ERROR, "NTP uncertainty measurement is unavailable"
            actual_distance = {"root_delay": delay, "root_dispersion": dispersion}
        results.append(
            _r(
                run_id,
                "chrony.uncertainty",
                site,
                server_id,
                {"max_root_distance_seconds": maximum},
                actual_distance,
                status,
                message,
                started,
            )
        )
    try:
        offset = abs(float(chrony["offset"]))
        if not math.isfinite(offset):
            raise ValueError("nonfinite offset")
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
