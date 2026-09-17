from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

import httpx

from app.collectors.portainer import (
    PortainerClient,
    PortainerClientSettings,
    PortainerCollector,
    PortainerError,
    normalize_swarm_site,
    sanitize_for_evidence,
)
from app.config.loader import load_config_dir
from app.config.validation import validate_config
from app.database.repository import Repository
from app.domain import CheckResult, CheckStatus
from app.evidence.manager import EvidenceManager
from app.orchestrator.aggregation import aggregate_status
from app.orchestrator.run_context import RunContext
from app.validators.portainer import PortainerValidator
from app.validators.site_parity import SiteParityValidator
from tests.fixtures.config_factory import load_fixture_config, valid_env


class PortainerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_fixture_config()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ctx = RunContext(
            "WR-20260811-000000",
            self.config,
            Repository("sqlite:///:memory:"),
            EvidenceManager(Path(self.tmp.name)),
        )
        self.addCleanup(self.ctx.repository.close)

    def fixture_sites(self) -> dict:
        return copy.deepcopy(self.config["portainer_expected"]["fixture_actual"]["sites"])

    def validate_sites(self, sites: dict) -> list[CheckResult]:
        return PortainerValidator().validate({"sites": sites, "errors": []}, self.config, self.ctx)

    def statuses(self, results: list[CheckResult], check_id: str) -> list[CheckStatus]:
        return [result.status for result in results if result.check_id == check_id]

    def parity(self, results: list[CheckResult]) -> list[CheckResult]:
        return SiteParityValidator().validate({"results": results}, self.config, self.ctx)

    def test_discovered_services_are_validated_independently(self):
        results = self.validate_sites(self.fixture_sites())
        self.assertEqual(
            self.statuses(results, "portainer.service.exists"),
            [CheckStatus.PASS, CheckStatus.PASS],
        )
        self.assertEqual(
            self.statuses(results, "portainer.service.desired_replicas"),
            [CheckStatus.PASS, CheckStatus.PASS],
        )
        self.assertEqual(
            self.statuses(results, "portainer.service.running_replicas"),
            [CheckStatus.PASS, CheckStatus.PASS],
        )
        self.assertEqual(
            self.statuses(results, "portainer.service.healthy_replicas"),
            [CheckStatus.PASS, CheckStatus.PASS],
        )
        self.assertEqual(
            self.statuses(results, "portainer.service.image"),
            [CheckStatus.PASS, CheckStatus.PASS],
        )
        self.assertEqual(
            self.statuses(results, "portainer.service.task_state"),
            [CheckStatus.PASS, CheckStatus.PASS],
        )

    def test_site_with_no_discovered_services_is_collection_error(self):
        sites = self.fixture_sites()
        sites["site1"]["services"] = []
        results = self.validate_sites(sites)
        discovery = [
            result
            for result in results
            if result.check_id == "portainer.discovery" and result.site == "site1"
        ][0]
        self.assertEqual(discovery.status, CheckStatus.ERROR)

    def test_running_less_than_desired_fails_independent_site_health(self):
        sites = self.fixture_sites()
        service = sites["site1"]["services"][0]
        service["desired_replicas"] = 3
        service["running_replicas"] = 2
        service["healthy_replicas"] = 2
        results = self.validate_sites(sites)
        running = [
            result
            for result in results
            if result.check_id == "portainer.service.running_replicas"
            and result.site == "site1"
        ][0]
        self.assertEqual(running.status, CheckStatus.FAIL)

    def test_health_unavailable_is_not_fabricated(self):
        sites = self.fixture_sites()
        service = sites["site1"]["services"][0]
        service["healthy_replicas"] = None
        service["health"] = {
            "available": False,
            "source": "not_available_from_response",
            "definition": "no health signal exposed",
        }
        results = self.validate_sites(sites)
        self.assertEqual(
            [
                result
                for result in results
                if result.check_id == "portainer.service.healthy_replicas"
                and result.site == "site1"
            ],
            [],
        )

    def test_failed_rejected_and_restarting_tasks_fail_task_state(self):
        for field in ["failed_tasks", "rejected_tasks", "restarting_tasks"]:
            with self.subTest(field=field):
                sites = self.fixture_sites()
                sites["site1"]["services"][0][field] = 1
                results = self.validate_sites(sites)
                task_state = [
                    result
                    for result in results
                    if result.check_id == "portainer.service.task_state" and result.site == "site1"
                ][0]
                self.assertEqual(task_state.status, CheckStatus.FAIL)

    def test_starting_tasks_follow_rules_policy(self):
        sites = self.fixture_sites()
        sites["site1"]["services"][0]["starting_tasks"] = 1
        results = self.validate_sites(sites)
        task_state = [
            result
            for result in results
            if result.check_id == "portainer.service.task_state" and result.site == "site1"
        ][0]
        self.assertEqual(task_state.status, CheckStatus.WARNING)

        config = copy.deepcopy(self.config)
        config["rules"]["portainer"]["task_state_policy"]["starting"] = "FAIL"
        task_state = [
            result
            for result in PortainerValidator().validate(
                {"sites": sites, "errors": []},
                config,
                self.ctx,
            )
            if result.check_id == "portainer.service.task_state" and result.site == "site1"
        ][0]
        self.assertEqual(task_state.status, CheckStatus.FAIL)

    def test_parity_uses_runtime_site_ids(self):
        config = copy.deepcopy(self.config)
        config["sites"]["sites"][0]["id"] = "primary"
        config["sites"]["sites"][1]["id"] = "secondary"
        config["portainer_expected"]["sites"]["primary"] = config["portainer_expected"][
            "sites"
        ].pop("site1")
        config["portainer_expected"]["sites"]["secondary"] = config["portainer_expected"][
            "sites"
        ].pop("site2")
        sites = self.fixture_sites()
        sites["primary"] = sites.pop("site1")
        sites["secondary"] = sites.pop("site2")
        for service in sites["primary"]["services"]:
            service["site"] = "primary"
        for service in sites["secondary"]["services"]:
            service["site"] = "secondary"
        results = PortainerValidator().validate(
            {"sites": sites, "errors": []},
            config,
            self.ctx,
        )
        parity = SiteParityValidator().validate(
            {"results": results},
            config,
            self.ctx,
        )
        self.assertTrue(parity)
        self.assertTrue(all(result.status == CheckStatus.PASS for result in parity))
        self.assertTrue(
            all(result.actual.get("sites") == ["primary", "secondary"] for result in parity)
        )

    def test_service_present_on_one_site_missing_from_other_fails_parity(self):
        sites = self.fixture_sites()
        sites["site2"]["services"][0]["name"] = "different-service"
        results = self.validate_sites(sites)
        parity = self.parity(results)
        presence = [
            result
            for result in parity
            if result.check_id == "parity.portainer.service_presence"
        ][0]
        self.assertEqual(presence.status, CheckStatus.FAIL)

    def test_image_mismatch_between_sites_fails_parity(self):
        sites = self.fixture_sites()
        sites["site2"]["services"][0]["image"] = "example/recording-gateway:v4"
        results = self.validate_sites(sites)
        image = [
            result for result in self.parity(results) if result.check_id == "parity.portainer.image"
        ][0]
        self.assertEqual(image.status, CheckStatus.FAIL)

    def test_replica_mismatch_between_sites_fails_parity(self):
        sites = self.fixture_sites()
        service = sites["site2"]["services"][0]
        service["desired_replicas"] = 2
        service["running_replicas"] = 2
        service["healthy_replicas"] = 2
        results = self.validate_sites(sites)
        parity = self.parity(results)
        desired = [
            result
            for result in parity
            if result.check_id == "parity.portainer.desired_replicas"
        ][0]
        running = [
            result
            for result in parity
            if result.check_id == "parity.portainer.running_replicas"
        ][0]
        self.assertEqual(desired.status, CheckStatus.FAIL)
        self.assertEqual(running.status, CheckStatus.FAIL)

    def test_identical_but_independently_unhealthy_sites_do_not_pass(self):
        sites = self.fixture_sites()
        for site in ("site1", "site2"):
            sites[site]["services"][0]["desired_replicas"] = 3
            sites[site]["services"][0]["running_replicas"] = 2
            sites[site]["services"][0]["healthy_replicas"] = 2
        results = self.validate_sites(sites)
        parity = self.parity(results)
        running_failures = [
            result for result in results if result.check_id == "portainer.service.running_replicas"
        ]
        parity_running = [
            result
            for result in parity
            if result.check_id == "parity.portainer.running_replicas"
        ][0]
        self.assertEqual(
            [result.status for result in running_failures],
            [CheckStatus.FAIL, CheckStatus.FAIL],
        )
        self.assertEqual(parity_running.status, CheckStatus.PASS)
        self.assertNotEqual(aggregate_status(results + parity, self.config), CheckStatus.PASS)

    def test_healthy_and_identical_sites_pass(self):
        results = self.validate_sites(self.fixture_sites())
        parity = self.parity(results)
        self.assertEqual(aggregate_status(results + parity, self.config), CheckStatus.PASS)

    def test_removed_image_reference_and_comparison_are_not_required(self):
        config = load_config_dir("deploy/docker/config", env=valid_env())
        report = validate_config(config, production_preflight=False)
        self.assertTrue(report.ok, report.lines())
        text = str(config["portainer_expected"])
        self.assertNotIn("reference", text)
        self.assertNotIn("comparison", text)

    def test_collection_errors_remain_collection_errors(self):
        actual = {
            "sites": {},
            "errors": [
                {
                    "site": "site1",
                    "code": "PORTAINER_AUTHENTICATION_ERROR",
                    "message": "authentication failed",
                    "retryable": False,
                }
            ],
        }
        results = PortainerValidator().validate(actual, self.config, self.ctx)
        self.assertEqual(results[0].status, CheckStatus.ERROR)
        self.assertEqual(results[0].metadata["error_code"], "PORTAINER_AUTHENTICATION_ERROR")
        self.assertNotEqual(results[0].check_id, "portainer.service.exists")

    def test_docker_swarm_response_normalization_keeps_counts_distinct(self):
        raw_services = [
            {
                "ID": "svc1",
                "Spec": {
                    "Name": "recording-gateway",
                    "Labels": {"com.docker.stack.namespace": "recording"},
                    "Mode": {"Replicated": {"Replicas": 3}},
                    "TaskTemplate": {
                        "ContainerSpec": {"Image": "example/recording-gateway:fixture"}
                    },
                },
            }
        ]
        raw_tasks = [
            {
                "ID": f"task{idx}",
                "ServiceID": "svc1",
                "DesiredState": "running",
                "Status": {
                    "State": "running",
                    "ContainerStatus": {"Health": {"Status": "healthy"}},
                },
            }
            for idx in range(1, 4)
        ]
        site = normalize_swarm_site(
            "site1",
            {"environment_type": "docker_swarm"},
            raw_services,
            raw_tasks,
            api_metadata={"version_probe": "fixture"},
        )
        service = site["services"][0]
        self.assertEqual(service["desired_replicas"], 3)
        self.assertEqual(service["running_replicas"], 3)
        self.assertEqual(service["healthy_replicas"], 3)
        self.assertTrue(service["health"]["available"])

    def test_sanitizer_removes_sensitive_headers_tokens_and_cookies(self):
        sanitized = sanitize_for_evidence(
            {
                "Authorization": "Bearer secret-token",
                "nested": {
                    "token": "secret-token",
                    "safe": "prefix secret-token suffix",
                    "cookies": "session=secret-token",
                },
            },
            ["secret-token"],
        )
        self.assertNotIn("secret-token", str(sanitized))
        self.assertIn("<REDACTED>", str(sanitized))

    def test_fixture_collector_returns_sanitized_evidence_payload(self):
        config = copy.deepcopy(self.config)
        config["portainer_expected"]["fixture_actual"]["sites"]["site1"]["raw_api"] = {
            "Authorization": "Bearer fixture-secret"
        }
        ctx = RunContext(
            self.ctx.run_id,
            config,
            self.ctx.repository,
            self.ctx.evidence,
        )
        actual = PortainerCollector().collect(ctx)
        self.assertEqual(actual["mode"], "fixture")
        self.assertNotIn("fixture-secret", str(actual))

    def test_client_retries_transient_get_and_never_mutates(self):
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.method)
            if len(calls) == 1:
                return httpx.Response(502, json={"temporary": True})
            return httpx.Response(200, json={"ok": True})

        client = PortainerClient(
            _settings(retries=2),
            transport=httpx.MockTransport(handler),
        )
        self.assertEqual(client.get_json("/api/status"), {"ok": True})
        self.assertEqual(calls, ["GET", "GET"])

    def test_client_classifies_auth_tls_timeout_and_invalid_json(self):
        cases = [
            (
                httpx.MockTransport(lambda request: httpx.Response(401, json={"err": "no"})),
                "PORTAINER_AUTHENTICATION_ERROR",
            ),
            (
                httpx.MockTransport(
                    lambda request: (_raise(httpx.ConnectError("CERTIFICATE_VERIFY_FAILED")))
                ),
                "PORTAINER_TLS_ERROR",
            ),
            (
                httpx.MockTransport(lambda request: (_raise(httpx.ReadTimeout("slow")))),
                "PORTAINER_TIMEOUT",
            ),
            (
                httpx.MockTransport(lambda request: httpx.Response(200, text="not json")),
                "PORTAINER_INVALID_RESPONSE",
            ),
        ]
        for transport, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(PortainerError) as raised:
                    PortainerClient(_settings(), transport=transport).get_json("/api/status")
                self.assertEqual(raised.exception.code, code)

    def test_unsupported_api_contract_is_isolated_collection_error(self):
        config = copy.deepcopy(self.config)
        config["portainer_expected"]["collection_mode"] = "live"
        config["portainer_expected"]["fixture_actual"] = None
        config["portainer_expected"]["sites"]["site1"]["connection"]["api_contract"] = (
            "unknown_contract"
        )
        actual = PortainerCollector().collect(
            RunContext(self.ctx.run_id, config, self.ctx.repository, self.ctx.evidence)
        )
        self.assertEqual(actual["errors"][0]["code"], "PORTAINER_UNSUPPORTED_API")

    def test_live_portainer_mode_requires_runtime_values_in_preflight(self):
        env = valid_env()
        env["PORTAINER_SITE1_URL"] = ""
        report = validate_config(load_config_dir("deploy/docker/config", env=env))
        self.assertFalse(report.ok)
        self.assertTrue(any("Portainer URL runtime value" in line for line in report.lines()))


def _settings(retries: int = 0) -> PortainerClientSettings:
    return PortainerClientSettings(
        site="site1",
        base_url="https://portainer.invalid",
        endpoint_id="1",
        auth_type="x_api_key",
        token="secret-token",
        tls_verify=True,
        connect_timeout=0.1,
        read_timeout=0.1,
        retries=retries,
        retry_backoff_seconds=0,
        api_contract="docker_proxy_v1",
    )


def _raise(exc: Exception):
    raise exc


if __name__ == "__main__":
    unittest.main()
