from __future__ import annotations

import copy
import re
from typing import Any

from app.collectors.base import Collector
from app.executors.ssh import SSHExecutor
from app.orchestrator.run_context import RunContext
from app.time_utils import iso_now

SYSTEM_TIME_RE = re.compile(
    r"System time\s*:\s*(?P<value>[+-]?\d+(?:\.\d+)?)\s+seconds\s+(?P<direction>slow|fast)",
    re.IGNORECASE,
)
LEAP_STATUS_RE = re.compile(r"Leap status\s*:\s*(?P<status>.+)", re.IGNORECASE)
SOURCE_RE = re.compile(
    r"^\s*[\^=#]\*\s+(?P<source>\S+)",
    re.MULTILINE,
)


class InfrastructureCollector(Collector):
    def __init__(self, ssh: SSHExecutor | None = None) -> None:
        self.ssh = ssh or SSHExecutor()

    def collect(self, context: RunContext) -> dict[str, Any]:
        config = context.config.get("servers", {})
        fixture = config.get("fixture_actual")
        if fixture is not None:
            return copy.deepcopy(fixture)
        return self._collect_live(config, context)

    def _collect_live(self, config: dict[str, Any], context: RunContext) -> dict[str, Any]:
        ssh_config = config.get("ssh") or {}
        sites: dict[str, Any] = {}
        for site_id, site in (config.get("sites") or {}).items():
            servers: dict[str, Any] = {}
            for server in site.get("servers", []):
                if not isinstance(server, dict):
                    continue
                server_id = str(server.get("id") or server.get("hostname") or "server")
                servers[server_id] = self._collect_server(server, ssh_config)
            sites[site_id] = {"servers": servers}
        return {
            "mode": "live",
            "collection_timestamp": iso_now(),
            "metadata": {
                "source": "ssh",
                "configuration_hash": context.config.get("_config_hash"),
                "read_only": True,
            },
            "sites": sites,
        }

    def _collect_server(
        self,
        server: dict[str, Any],
        ssh_config: dict[str, Any],
    ) -> dict[str, Any]:
        hostname = str(server.get("hostname") or "")
        port = int(server.get("ssh_port") or 22)
        username = str(ssh_config.get("username") or "")
        password = str(ssh_config.get("password") or "")
        known_hosts_path = str(ssh_config.get("known_hosts_path") or "")
        connect_timeout = int(ssh_config.get("connect_timeout") or 5)
        command_timeout = int(ssh_config.get("command_timeout") or 5)
        payload: dict[str, Any] = {
            "reachable": False,
            "commands": {},
        }

        try:
            filesystem = (server.get("filesystems") or [{}])[0]
            df_command = filesystem.get("command") or "df -h | grep mapper"
            df = self.ssh.run(
                host=hostname,
                port=port,
                username=username,
                command=str(df_command),
                connect_timeout=connect_timeout,
                command_timeout=command_timeout,
                password=password,
                known_hosts_path=known_hosts_path,
            )
            payload["commands"]["df"] = {
                "exit_code": df.exit_code,
                "stderr": df.stderr,
                "timeout": df.timeout,
            }
            # Preserve the legacy SSH failure status; None means transport,
            # host verification, authentication, or timeout failure.
            if df.exit_code in (None, 255):
                payload["reachable"] = False
                payload["df_error"] = (
                    df.stderr or "SSH connection/authentication/host verification failed"
                )
                return payload
            # SSH itself worked.
            payload["reachable"] = True
            # Keep raw output as evidence, including partial output on failure.
            payload["df"] = df.stdout
            # The pipeline returns grep's status. Diagnostics can expose a df
            # failure even when grep returns 0 or 1; never trust partial data.
            if df.stderr.strip():
                payload["df_error"] = df.stderr
            elif df.exit_code == 1 and df.stdout.strip():
                payload["df_error"] = "grep reported no matches but returned output"
            elif df.exit_code not in (0, 1):
                payload["df_error"] = f"mapper filesystem command exited {df.exit_code}"
        except Exception:
            payload["reachable"] = False
            payload["df_error"] = "SSH collection failed"
            return payload

        try:
            timezone = self.ssh.run(
                host=hostname,
                port=port,
                username=username,
                command="timedatectl show -p Timezone --value",
                connect_timeout=connect_timeout,
                command_timeout=command_timeout,
                password=password,
                known_hosts_path=known_hosts_path,
            )
            tracking = self.ssh.run(
                host=hostname,
                port=port,
                username=username,
                command="chronyc tracking",
                connect_timeout=connect_timeout,
                command_timeout=command_timeout,
                password=password,
                known_hosts_path=known_hosts_path,
            )
            sources = self.ssh.run(
                host=hostname,
                port=port,
                username=username,
                command="chronyc sources -n",
                connect_timeout=connect_timeout,
                command_timeout=command_timeout,
                password=password,
                known_hosts_path=known_hosts_path,
            )
            payload["commands"]["timezone"] = {"exit_code": timezone.exit_code}
            payload["commands"]["chronyc_tracking"] = {"exit_code": tracking.exit_code}
            payload["commands"]["chronyc_sources"] = {"exit_code": sources.exit_code}
            if timezone.exit_code == tracking.exit_code == sources.exit_code == 0:
                payload["chrony"] = {
                    **normalize_chrony(tracking.stdout, sources.stdout),
                    "timezone": timezone.stdout.strip(),
                }
            else:
                payload["chrony_error"] = "one or more Chrony/timezone commands failed"
        except Exception:
            payload["chrony_error"] = "Chrony/timezone collection failed"
        return payload


def parse_chronyc_tracking(text: str) -> dict[str, Any]:
    system_time = SYSTEM_TIME_RE.search(text)
    leap_status = LEAP_STATUS_RE.search(text)
    if system_time is None or leap_status is None:
        raise ValueError("chronyc tracking output missing System time or Leap status")
    offset = float(system_time.group("value"))
    if system_time.group("direction").lower() == "fast":
        offset = -offset
    leap = leap_status.group("status").strip()
    return {
        "offset": offset,
        "leap_status": leap,
        "synchronized": leap.lower() == "normal",
    }


def parse_chronyc_sources(text: str) -> dict[str, Any]:
    selected = SOURCE_RE.search(text)
    if selected is None:
        raise ValueError("chronyc sources output did not contain a selected source")
    return {"source": selected.group("source"), "selected": True}


def normalize_chrony(tracking_text: str, sources_text: str) -> dict[str, Any]:
    tracking = parse_chronyc_tracking(tracking_text)
    sources = parse_chronyc_sources(sources_text)
    return {
        "synchronized": bool(tracking["synchronized"] and sources["selected"]),
        "source": sources["source"],
        "offset": tracking["offset"],
        "raw": {
            "tracking": tracking,
            "sources": sources,
        },
    }
