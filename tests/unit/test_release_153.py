from __future__ import annotations

import io
import json
import os
import socket
import ssl
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from pypdf import PdfReader

from app.api import dependencies
from app.collectors.doctor import DoctorCollector
from app.collectors.infrastructure import normalize_chrony
from app.collectors.portainer import PortainerCollector, PortainerError, normalize_swarm_site
from app.collectors.rabbitmq import _normalize_node
from app.config.loader import load_config_dir
from app.config.validation import validate_config
from app.database.repository import Repository
from app.domain import (
    CheckResult,
    CheckStatus,
    ManualDBReview,
    ManualDBReviewResult,
    NoteScope,
    ReviewDecision,
    ReviewNote,
    RunState,
)
from app.evidence.manager import EvidenceManager
from app.evidence.redaction import sanitize
from app.observability import event
from app.orchestrator.lock import InvalidRunTransition
from app.orchestrator.run_context import RunContext
from app.reporting.final_pdf import render_final_pdf
from app.reporting.html import render_final_html
from app.reporting.snapshot import finalize_run
from app.security import issue_csrf_token
from app.validators.doctor import DoctorValidator
from app.validators.infrastructure import _chrony_results, _filesystem_results
from app.validators.portainer import PortainerValidator
from app.validators.rabbitmq import _node_results
from app.web.main import create_app
from tests.fixtures.config_factory import load_fixture_config, valid_env


def service(mode=None, template=None):
    return {
        "ID": "s1",
        "Spec": {
            "Name": "nginx_nginx",
            "Mode": mode or {"Global": {}},
            "TaskTemplate": {"ContainerSpec": {"Image": "nginx:fixture"}, **(template or {})},
        },
    }


def node(identity="n1", availability="active", state="ready", labels=None):
    return {
        "ID": identity,
        "Spec": {"Availability": availability, "Role": "worker", "Labels": labels or {}},
        "Status": {"State": state},
        "Description": {
            "Hostname": identity,
            "Platform": {"OS": "linux", "Architecture": "x86_64"},
            "Engine": {"Labels": {}},
        },
    }


def task(state="running", desired="running", node_id="n1", health=None):
    status = {"State": state}
    if health:
        status["ContainerStatus"] = {"Health": {"Status": health}}
    return {
        "ID": "task-" + state + node_id,
        "ServiceID": "s1",
        "NodeID": node_id,
        "DesiredState": desired,
        "Status": status,
    }


class Release153Collectors(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Repository()
        self.addCleanup(self.repo.close)
        self.config = load_fixture_config()
        self.evidence = EvidenceManager(self.temp.name)
        self.context = RunContext("WR-153", self.config, self.repo, self.evidence)

    def normalized(self, svc=None, tasks=None, nodes=None):
        return normalize_swarm_site(
            "site1", {}, [svc or service()], tasks or [], raw_nodes=nodes, api_metadata={}
        )["services"][0]

    def test_global_eligible_nodes_constraints_platform_and_availability(self):
        nodes = [
            node(),
            node("n2", labels={"edge": "true"}),
            node("n3", "drain"),
            node("n4", state="down"),
        ]
        self.assertEqual(self.normalized(nodes=nodes)["desired_replicas"], 2)
        constrained = service(
            template={
                "Placement": {"Constraints": ["node.labels.edge == true", "node.role != manager"]}
            }
        )
        self.assertEqual(self.normalized(constrained, nodes=nodes)["desired_replicas"], 1)
        constrained["Spec"]["TaskTemplate"]["Placement"]["Platforms"] = [
            {"OS": "windows", "Architecture": "amd64"}
        ]
        self.assertEqual(self.normalized(constrained, nodes=nodes)["desired_replicas"], 0)
        constrained["Spec"]["TaskTemplate"]["Placement"]["Platforms"] = [
            {"OS": "linux", "Architecture": "amd64"}
        ]
        self.assertEqual(self.normalized(constrained, nodes=nodes)["desired_replicas"], 1)

    def test_global_pause_retains_existing_tasks(self):
        self.assertEqual(self.normalized(nodes=[node(availability="pause")])["desired_replicas"], 0)
        self.assertEqual(
            self.normalized(tasks=[task()], nodes=[node(availability="pause")])["desired_replicas"],
            1,
        )

    def test_global_unavailable_evidence_is_not_fabricated(self):
        for nodes, template in (
            (None, {}),
            ([{}], {}),
            ([node()], {"Placement": {"Constraints": ["unsupported.key == yes"]}}),
            ([node()], {"Resources": {"Reservations": {"NanoCPUs": 100}}}),
        ):
            with self.subTest(nodes=nodes, template=template):
                normalized = self.normalized(service(template=template), nodes=nodes)
                self.assertIsNone(normalized["desired_replicas"])
                self.assertFalse(normalized["eligibility"]["available"])

    def test_replicated_counts_and_historical_failures(self):
        normalized = self.normalized(
            service({"Replicated": {"Replicas": 2}}),
            [
                task(health="healthy"),
                task(node_id="n2", health="healthy"),
                task("failed", "shutdown"),
                task("shutdown", "shutdown"),
            ],
        )
        self.assertEqual(
            (
                normalized["desired_replicas"],
                normalized["running_replicas"],
                normalized["healthy_replicas"],
            ),
            (2, 2, 2),
        )
        self.assertEqual(normalized["failed_tasks"], 0)
        self.assertEqual(len(normalized["historical_task_failures"]), 1)
        normalized = self.normalized(service({"Replicated": {"Replicas": 2}}), [task()])
        self.assertEqual(normalized["service_state"], "degraded")

    def test_current_failures_are_retained_and_partial_health_is_unknown(self):
        for state in ("failed", "rejected", "restarting", "starting"):
            with self.subTest(state=state):
                normalized = self.normalized(tasks=[task(state)], nodes=[node()])
                self.assertEqual(normalized[state + "_tasks"], 1)
        normalized = self.normalized(
            tasks=[task(health="healthy"), task(node_id="n2")], nodes=[node(), node("n2")]
        )
        self.assertIsNone(normalized["healthy_replicas"])
        self.config["rules"]["portainer"]["health_required"] = True
        results = PortainerValidator().validate(
            {"sites": {"site1": {"services": [normalized]}}}, self.config, self.context
        )
        health = [r for r in results if r.check_id.endswith("healthy_replicas")]
        self.assertEqual(health[0].status, CheckStatus.ERROR)

    def test_malformed_api_fields(self):
        for svc, tasks in (
            ({"Spec": []}, []),
            (service(), [None]),
            (service(), [{"ServiceID": "s1"}]),
            (
                {"ID": "x", "Spec": {"Name": "x", "Mode": {"Replicated": []}, "TaskTemplate": []}},
                [],
            ),
        ):
            with self.subTest(svc=svc, tasks=tasks):
                with self.assertRaises(PortainerError):
                    normalize_swarm_site("site1", {}, [svc], tasks, api_metadata={})

    def test_live_inventory_21_services_two_sites_reads_nodes(self):
        self.config["portainer_expected"].pop("fixture_actual", None)
        self.config["portainer_expected"]["collection_mode"] = "live"
        services, tasks = [], []
        for i in range(21):
            svc = service({"Replicated": {"Replicas": 1}} if i < 19 else {"Global": {}})
            svc["ID"] = f"s{i}"
            svc["Spec"]["Name"] = (
                f"svc{i}" if i < 19 else ("nginx_nginx" if i == 19 else "portainer_agent")
            )
            services.append(svc)
            item = task()
            item["ServiceID"] = f"s{i}"
            tasks.append(item)
        methods = []

        def handler(request):
            methods.append(request.method)
            return httpx.Response(
                200,
                json=services
                if request.url.path.endswith("/services")
                else tasks
                if request.url.path.endswith("/tasks")
                else [node()]
                if request.url.path.endswith("/nodes")
                else {},
            )

        actual = PortainerCollector(httpx.MockTransport(handler)).collect(self.context)
        self.assertEqual(actual["errors"], [])
        self.assertTrue(all(len(site["services"]) == 21 for site in actual["sites"].values()))
        results = PortainerValidator().validate(actual, self.config, self.context)
        self.assertFalse(any(r.status == CheckStatus.ERROR for r in results))
        self.assertEqual(set(methods), {"GET"})

    def doctor(self, handler, connection=None):
        doctor = self.config["doctor"]["doctor"]
        doctor.pop("fixture_actual", None)
        doctor["api"] = {
            "sites": {"site1": "https://doctor.invalid/verified?token=hidden"},
            "connections": {"site1": connection or {}},
        }
        self.config["sites"]["sites"] = [{"id": "site1"}]
        return DoctorCollector(httpx.MockTransport(handler)).collect(self.context)

    def test_doctor_http_failure_categories(self):
        for status, code, retryable in (
            (401, "DOCTOR_AUTH_ERROR", False),
            (403, "DOCTOR_AUTH_ERROR", False),
            (404, "DOCTOR_HTTP_ERROR", False),
            (500, "DOCTOR_HTTP_ERROR", True),
        ):
            with self.subTest(status=status):
                actual = self.doctor(lambda request, code=status: httpx.Response(code))
                error = actual["errors"][0]
                self.assertEqual(
                    (error["code"], error["status_code"], error["retryable"]),
                    (code, status, retryable),
                )
                self.assertEqual(error["endpoint"], "https://doctor.invalid/verified")
                self.assertEqual(error["site"], "site1")
                self.assertTrue(error["collection_timestamp"])

    def test_doctor_transport_failures(self):
        cases = [
            (socket.gaierror("getaddrinfo failed"), "DOCTOR_DNS_ERROR"),
            (ConnectionRefusedError("refused"), "DOCTOR_CONNECTION_REFUSED"),
            (ssl.SSLError("certificate failed"), "DOCTOR_TLS_ERROR"),
        ]
        for cause, code in cases:

            def handler(request, underlying=cause):
                raise httpx.ConnectError(
                    "transport failed password=neverlog", request=request
                ) from underlying

            with self.subTest(code=code):
                actual = self.doctor(handler)
                self.assertEqual(actual["errors"][0]["code"], code)
                self.assertNotIn("neverlog", json.dumps(actual))

        def timeout(request):
            raise httpx.ReadTimeout("slow")

        self.assertEqual(self.doctor(timeout)["errors"][0]["code"], "DOCTOR_TIMEOUT")

    def test_doctor_success_unhealthy_and_missing_inventory(self):
        self.config["doctor"]["doctor"]["expected_services"] = ["gateway", "api"]
        payload = [{"name": "gateway", "status": "Healthy"}, {"name": "api", "status": 2}]
        actual = self.doctor(lambda request: httpx.Response(200, json=payload))
        result = DoctorValidator().validate(actual, self.config, self.context)
        self.assertEqual(result[-1].status, CheckStatus.PASS)
        payload[1]["status"] = 0
        actual = self.doctor(lambda request: httpx.Response(200, json=payload))
        result = DoctorValidator().validate(actual, self.config, self.context)
        self.assertEqual(result[-1].status, CheckStatus.MANUAL_REVIEW)
        payload.pop()
        actual = self.doctor(lambda request: httpx.Response(200, json=payload))
        result = DoctorValidator().validate(actual, self.config, self.context)
        self.assertEqual(result[-1].status, CheckStatus.ERROR)
        self.assertTrue(
            any(r.metadata.get("error_code") == "DOCTOR_INCOMPLETE_RESPONSE" for r in result)
        )

    def test_doctor_malformed_json_and_secret_redaction(self):
        actual = self.doctor(lambda request: httpx.Response(200, text="<html>frontend</html>"))
        self.assertEqual(actual["errors"][0]["code"], "DOCTOR_INVALID_RESPONSE")

        def handler(request):
            self.assertEqual(request.headers["Authorization"], "Bearer opaque-sentinel")
            return httpx.Response(
                200,
                json=[
                    {
                        "name": "api",
                        "status": "Unhealthy",
                        "entries": [{"name": "db", "status": 0, "description": "opaque-sentinel"}],
                    }
                ],
            )

        actual = self.doctor(handler, {"auth_type": "bearer_token", "token": "opaque-sentinel"})
        self.assertNotIn("opaque-sentinel", json.dumps(actual))
        actual = self.doctor(handler, {"tls_verify": False})
        self.assertEqual(actual["errors"][0]["code"], "DOCTOR_CONFIGURATION_ERROR")

    def test_doctor_browser_fragment_rejected_before_network(self):
        env = valid_env()
        env["DOCTOR_SITE1_API_URL"] = "https://doctor.invalid/doctor#/healthchecks"
        config = load_config_dir("deploy/docker/config", env=env)
        self.assertFalse(validate_config(config, production_preflight=False).ok)
        self.config = config
        context = RunContext("WR-153", config, self.repo, self.evidence)

        def handler(request):
            self.assertNotIn("doctor.invalid", str(request.url))
            return httpx.Response(200, json=[{"name": "api", "status": 2}])

        actual = DoctorCollector(httpx.MockTransport(handler)).collect(context)
        self.assertEqual(actual["errors"][0]["code"], "DOCTOR_CONFIGURATION_ERROR")

    def test_rabbitmq_measurements_and_thresholds(self):
        for metrics, status in (
            ({"sockets_used": 1, "sockets_total": 100}, CheckStatus.PASS),
            ({"sockets_used": 0, "sockets_total": 0}, CheckStatus.ERROR),
            ({"sockets_used": None, "sockets_total": 100}, CheckStatus.ERROR),
            ({}, CheckStatus.ERROR),
            ({"sockets_used": 100, "sockets_total": 100}, CheckStatus.ERROR),
        ):
            with self.subTest(metrics=metrics):
                result = _node_results(
                    "WR-153",
                    "site1",
                    {},
                    _normalize_node({"name": "rabbit", **metrics}),
                    CheckStatus.ERROR,
                    "now",
                )
                sockets = next(r for r in result if r.check_id.endswith("socket_descriptors"))
                self.assertEqual(sockets.status, status)
        observed = _normalize_node(
            {
                "fd_used": 100,
                "fd_total": 100,
                "proc_used": 100,
                "proc_total": 100,
                "disk_free_alarm": True,
            }
        )
        self.assertEqual(
            observed["resource_states"],
            {"file_descriptors": "red", "erlang_processes": "red", "disk_space": "red"},
        )

    def test_chrony_local_uncertainty_unreachable_and_utc_policy(self):
        actual = normalize_chrony(
            "Reference ID : 7F7F0101 ()\nSystem time : 0.0001 seconds slow\n"
            "Leap status : Normal\nRoot delay : 0.01 seconds\nRoot dispersion : 10.78 seconds",
            "^? dc1 1 6 0 12 +10us",
        )
        actual["timezone"] = "Etc/UTC"
        expected = {
            "source": "dc1",
            "timezone": "Etc/UTC",
            "local_reference_status": "WARNING",
            "warning_offset": 0.5,
            "critical_offset": 1,
            "max_root_distance_seconds": 1,
        }
        result = _chrony_results("WR-153", "site1", "server", expected, {"chrony": actual}, "now")
        messages = " ".join(r.message for r in result)
        for code in (
            "NTP_LOCAL_REFERENCE_ACTIVE",
            "NTP_EXCESSIVE_UNCERTAINTY",
            "NTP_SOURCE_UNREACHABLE",
        ):
            self.assertIn(code, messages)
        self.assertNotIn("NTP_SOURCE_MISMATCH", messages)
        self.assertEqual(result[0].status, CheckStatus.PASS)
        actual.update(local_reference=False, synchronized=False, source="other", timezone="Wrong")
        result = _chrony_results("WR-153", "site1", "server", expected, {"chrony": actual}, "now")
        messages = " ".join(r.message for r in result)
        for code in ("NTP_UNSYNCHRONIZED", "NTP_SOURCE_MISMATCH", "TIMEZONE_MISMATCH"):
            self.assertIn(code, messages)

    def test_root_95_percent_stays_fail(self):
        result = _filesystem_results(
            "WR-153",
            "site1",
            "server",
            {"match": "root", "warning_percent": 70, "critical_percent": 80},
            {
                "df": "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
                "/dev/sda1 100 95 5 95% /\n"
            },
            "now",
        )
        self.assertEqual(result[0].status, CheckStatus.FAIL)
        self.assertEqual(result[0].actual["mountpoint"], "/")

    def test_secrets_are_absent_from_evidence_and_logs(self):
        payload = {
            "nested": {
                "api_key": "api-sentinel",
                "passwd": "pass-sentinel",
                "private_key": "private-sentinel",
            },
            "Env": [
                "PASSWORD=env-sentinel",
                "mysqlConnection=server=db;password=sql-sentinel",
                "NORMAL=value",
            ],
            "detail": "connection failed https://user:url-sentinel@example.invalid "
                      "password=inline-sentinel",
        }
        captured = io.StringIO()
        with redirect_stdout(captured):
            record = self.evidence.write_json("WR-153", "portainer", "site1", "raw.json", payload)
            event("test", **payload)
        content = self.evidence.absolute(record.path).read_text() + captured.getvalue()
        for secret in (
            "api-sentinel",
            "pass-sentinel",
            "private-sentinel",
            "env-sentinel",
            "sql-sentinel",
            "url-sentinel",
            "inline-sentinel",
        ):
            self.assertNotIn(secret, content)
        self.assertIn("NORMAL=value", content)
        self.assertEqual(sanitize({"token": "x"})["token"], "<REDACTED>")


class Release153Review(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Repository()
        self.addCleanup(self.repo.close)
        self.evidence = EvidenceManager(self.temp.name)
        self.config = load_fixture_config()
        self.config["rules"]["review"].update(required_module_notes=[], general_note_required=False)
        self.config["splunk_dashboards"]["dashboards"] = []
        for name, policy in self.config["rules"]["modules"].items():
            policy["enabled"] = name == "portainer"
            policy["required"] = name == "portainer"
        app = create_app()
        app.dependency_overrides[dependencies.get_repository] = lambda: self.repo
        app.dependency_overrides[dependencies.get_config] = lambda: self.config
        app.dependency_overrides[dependencies.get_evidence_manager] = lambda: self.evidence
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def ready(self, module="portainer", status=CheckStatus.PASS, run_id="WR-153"):
        self.repo.create_run(started_by="tester", run_id=run_id)
        self.repo.claim_next_run("fixture")
        self.repo.add_result(
            CheckResult(run_id, module, module + ".test", status, "fixture", site="site1")
        )
        self.repo.mark_review_ready(run_id, status)
        self.repo.save_manual_db_review(
            ManualDBReview(
                run_id,
                "Database",
                "C:\\Scripts\\check.ps1",
                ManualDBReviewResult.PASS,
                "Execution completed; replication PASS; cleanup verified; "
                "evidence retained locally.",
                "tester",
            )
        )
        return run_id

    def test_reject_with_db_pass_and_other_module_failures(self):
        for index, (module, status) in enumerate(
            (
                ("portainer", CheckStatus.PASS),
                ("portainer", CheckStatus.FAIL),
                ("infrastructure", CheckStatus.FAIL),
                ("doctor", CheckStatus.ERROR),
                ("rabbitmq", CheckStatus.FAIL),
            )
        ):
            with self.subTest(module=module, status=status):
                run_id = self.ready(module, status, f"WR-153-{index}")
                snapshot = finalize_run(
                    self.repo,
                    self.evidence,
                    self.config,
                    run_id,
                    "Operator",
                    ReviewDecision.REJECT,
                    rejection_reason="Operational issue",
                )
                run = self.repo.get_run(run_id)
                self.assertEqual(run.state, RunState.REJECTED)
                self.assertEqual(run.rejection_reason, "Operational issue")
                self.assertEqual(self.repo.list_results(run_id)[0].status, status)
                self.assertEqual(snapshot["run"]["reviewed_at"], run.reviewed_at)
                self.assertEqual(snapshot["run"]["state"], "REJECTED")
                self.assertIn("Operational issue", render_final_html(snapshot))
                text = "".join(
                    page.extract_text()
                    for page in PdfReader(self.evidence.absolute(run.final_pdf_path)).pages
                )
                self.assertIn("Operational issue", text)
                with self.assertRaises(InvalidRunTransition):
                    finalize_run(
                        self.repo,
                        self.evidence,
                        self.config,
                        run_id,
                        "Operator",
                        ReviewDecision.REJECT,
                        rejection_reason="again",
                    )

    def test_reason_identity_csrf_policy_and_recovery(self):
        run_id = self.ready(status=CheckStatus.FAIL)
        endpoint = f"/api/runs/{run_id}/finalize"
        with patch.dict(os.environ, {"WEEKEND_REPORT_CSRF_SIGNING_KEY": "fixture-key"}):
            self.assertEqual(
                self.client.post(
                    endpoint, json={"decision": "REJECT", "reviewer": "Operator"}
                ).status_code,
                403,
            )
            headers = {"X-CSRF-Token": issue_csrf_token()}
            for payload in (
                {"decision": "REJECT", "reviewer": "Operator"},
                {"decision": "REJECT", "rejection_reason": "why"},
                {"decision": "APPROVE", "reviewer": "Operator"},
            ):
                self.assertEqual(
                    self.client.post(endpoint, json=payload, headers=headers).status_code, 400
                )
            self.config["rules"]["review"]["reject_allowed"] = False
            payload = {"decision": "REJECT", "reviewer": "Operator", "rejection_reason": "why"}
            self.assertEqual(
                self.client.post(endpoint, json=payload, headers=headers).status_code, 400
            )
            self.config["rules"]["review"]["reject_allowed"] = True
            self.repo._execute(
                "UPDATE runs SET state='RECOVERY_REQUIRED' WHERE run_id=?", (run_id,)
            )
            self.assertEqual(
                self.client.post(endpoint, json=payload, headers=headers).status_code, 400
            )
            self.assertEqual(self.repo.get_run(run_id).state, RunState.RECOVERY_REQUIRED)

    def test_pdf_retry_uses_original_snapshot_and_freezes_notes(self):
        run_id = self.ready()
        with self.assertRaises(RuntimeError):
            finalize_run(
                self.repo,
                self.evidence,
                self.config,
                run_id,
                "Operator",
                ReviewDecision.REJECT,
                rejection_reason="why",
                fail_pdf=True,
            )
        original = self.evidence.absolute(
            self.repo.get_run(run_id).final_snapshot_path
        ).read_bytes()
        with self.assertRaises(InvalidRunTransition):
            self.repo.save_note(ReviewNote(run_id, NoteScope.GENERAL, "operator", "changed"))
        with self.assertRaises(ValueError):
            finalize_run(
                self.repo, self.evidence, self.config, run_id, "Operator", ReviewDecision.APPROVE
            )
        finalize_run(
            self.repo,
            self.evidence,
            self.config,
            run_id,
            "Operator",
            ReviewDecision.REJECT,
            rejection_reason="why",
        )
        self.assertEqual(
            original,
            self.evidence.absolute(self.repo.get_run(run_id).final_snapshot_path).read_bytes(),
        )

    def test_review_page_exposes_independent_actions_and_reason(self):
        run_id = self.ready()
        page = self.client.get(f"/runs/{run_id}/review")
        self.assertEqual(page.status_code, 200)
        self.assertIn("data-rejection-reason", page.text)
        self.assertIn("A database PASS allows rejection", page.text)
        self.assertIn("APPROVE requirements", page.text)

    def test_large_pdf_preserves_long_text_and_pagination(self):
        snapshot = {
            "run": {"run_id": "WR-PDF-153", "application_version": "v1.5.3"},
            "review": {
                "reviewer": "Operator",
                "decision": "REJECT",
                "rejection_reason": "Rejected with evidence",
                "confirmed_at": "2026-11-08",
            },
            "sites": [{"id": "site1"}, {"id": "site2"}],
            "overall_status": "ERROR",
            "results": [
                {
                    "id": i,
                    "module": "doctor",
                    "site": "site1",
                    "target": "long-service-" * 30,
                    "check_id": "doctor.health",
                    "status": "ERROR" if i == 0 else "PASS",
                    "message": "Long error details " * 500 + "END-OF-ERROR",
                    "actual": {"healthy": False},
                }
                for i in range(250)
            ],
        }
        path = Path(self.temp.name) / "large.pdf"
        render_final_pdf(snapshot, path)
        reader = PdfReader(path)
        text = "\n".join(page.extract_text() for page in reader.pages)
        self.assertGreater(len(reader.pages), 5)
        self.assertIn("END-OF-ERROR", text)
        self.assertIn("Rejected with evidence", text)
        self.assertIn("Module Summary", text)
        self.assertTrue(all("Page " in page.extract_text() for page in reader.pages))
