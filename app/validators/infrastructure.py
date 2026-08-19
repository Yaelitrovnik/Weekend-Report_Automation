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
            server_outputs = site_actual.get("servers", {})
            for server in site_expected.get("servers", []):
                server_id = server["id"]
                outputs = server_outputs.get(server_id, {})
                if not outputs.get("reachable", True):
                    results.append(
                        _r(
                            context.run_id,
                            "ssh.reachable",
                            site,
                            server_id,
                            {"reachable": True},
                            outputs,
                            CheckStatus.ERROR,
                            "server unreachable",
                            started,
                        )
                    )
                df_rows = parse_df(outputs.get("df", ""))
                by_mount = {row["mountpoint"]: row for row in df_rows}
                for fs in server.get("filesystems", []):
                    row = by_mount.get(fs["mountpoint"])
                    if not row:
                        results.append(
                            _r(
                                context.run_id,
                                "filesystem.exists",
                                site,
                                server_id,
                                fs,
                                None,
                                CheckStatus.FAIL,
                                "expected filesystem missing",
                                started,
                            )
                        )
                        continue
                    status = threshold_status(
                        float(row["utilization_percent"]),
                        float(fs["warning_percent"]),
                        float(fs["critical_percent"]),
                    )
                    results.append(
                        _r(
                            context.run_id,
                            "filesystem.utilization",
                            site,
                            server_id,
                            fs,
                            row,
                            status,
                            f"{row['mountpoint']} utilization {row['utilization_percent']}%",
                            started,
                        )
                    )
                results.extend(
                    _validate_nfs_mounts(
                        context.run_id,
                        site,
                        server_id,
                        server.get("nfs_mounts", []),
                        outputs,
                        df_rows,
                        started,
                    )
                )
                chrony = outputs.get("chrony", {})
                for ntp in server.get("chrony", []):
                    synced = bool(chrony.get("synchronized"))
                    status = CheckStatus.PASS if synced else CheckStatus.FAIL
                    results.append(
                        _r(
                            context.run_id,
                            "chrony.synchronized",
                            site,
                            server_id,
                            ntp,
                            chrony,
                            status,
                            "chrony synchronized" if synced else "chrony unsynchronized",
                            started,
                        )
                    )
                    expected_source = ntp.get("source")
                    actual_source = chrony.get("source")
                    if expected_source:
                        status = (
                            CheckStatus.PASS
                            if actual_source == expected_source
                            else CheckStatus.FAIL
                        )
                        results.append(
                            _r(
                                context.run_id,
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
                    offset = abs(float(chrony.get("offset", 999999)))
                    if synced:
                        status = threshold_status(
                            offset, float(ntp["warning_offset"]), float(ntp["critical_offset"])
                        )
                        results.append(
                            _r(
                                context.run_id,
                                "chrony.offset",
                                site,
                                server_id,
                                ntp,
                                chrony,
                                status,
                                f"absolute offset {offset}",
                                started,
                            )
                        )
        return results


def _validate_nfs_mounts(
    run_id: str,
    site: str,
    server_id: str,
    expected_mounts: Any,
    outputs: dict[str, Any],
    df_rows: list[dict[str, Any]],
    started: str,
) -> list[CheckResult]:
    if not isinstance(expected_mounts, list):
        return []
    actual_mounts = _nfs_by_mountpoint(outputs.get("nfs_mounts", []), df_rows)
    results: list[CheckResult] = []
    for expected in expected_mounts:
        if not isinstance(expected, dict):
            continue
        mountpoint = expected.get("mountpoint") or expected.get("destination")
        source = expected.get("source")
        target = str(mountpoint or source or server_id)
        actual = actual_mounts.get(str(mountpoint))
        required = bool(expected.get("required", True))
        if actual is None:
            results.append(
                _r(
                    run_id,
                    "nfs.exists",
                    site,
                    server_id,
                    expected,
                    {"exists": False},
                    CheckStatus.FAIL if required else CheckStatus.SKIPPED,
                    "expected NFS mount missing" if required else "optional NFS mount absent",
                    started,
                )
            )
            continue
        results.append(
            _r(
                run_id,
                "nfs.exists",
                site,
                server_id,
                expected,
                {"exists": True, **actual},
                CheckStatus.PASS,
                "NFS mount exists",
                started,
            )
        )
        if source:
            status = CheckStatus.PASS if actual.get("source") == source else CheckStatus.FAIL
            results.append(
                _r(
                    run_id,
                    "nfs.source",
                    site,
                    server_id,
                    {"source": source, "mountpoint": mountpoint},
                    actual,
                    status,
                    "NFS source matches expected"
                    if status == CheckStatus.PASS
                    else "NFS source mismatch",
                    started,
                )
            )
        usable = actual.get("usable", actual.get("reachable", True))
        status = CheckStatus.PASS if usable is True else CheckStatus.FAIL
        results.append(
                _r(
                    run_id,
                    "nfs.usable",
                site,
                server_id,
                {"usable": True, "mountpoint": mountpoint},
                    actual,
                    status,
                    "NFS mount is reachable/usable"
                    if status == CheckStatus.PASS
                    else "NFS mount is not usable",
                    started,
                )
            )
        if actual.get("utilization_percent") is not None:
            status = threshold_status(
                float(actual["utilization_percent"]),
                float(expected["warning_percent"]),
                float(expected["critical_percent"]),
            )
            results.append(
                _r(
                    run_id,
                    "nfs.utilization",
                    site,
                    server_id,
                    expected,
                    actual,
                    status,
                    f"{target} NFS utilization {actual['utilization_percent']}%",
                    started,
                )
            )
    return results


def _nfs_by_mountpoint(
    actual_mounts: Any, df_rows: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for row in df_rows:
        filesystem = str(row.get("filesystem", ""))
        if ":" in filesystem:
            rows[str(row["mountpoint"])] = {
                "source": filesystem,
                "mountpoint": row["mountpoint"],
                "destination": row["mountpoint"],
                "utilization_percent": row["utilization_percent"],
                "reachable": True,
                "usable": True,
            }
    if not isinstance(actual_mounts, list):
        return rows
    for mount in actual_mounts:
        if not isinstance(mount, dict):
            continue
        mountpoint = mount.get("mountpoint") or mount.get("destination")
        if mountpoint:
            rows[str(mountpoint)] = {**rows.get(str(mountpoint), {}), **mount}
    return rows


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
