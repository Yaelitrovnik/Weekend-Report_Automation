from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from app.collectors.portainer import (
    PortainerClient,
    PortainerClientSettings,
    PortainerCollector,
    PortainerError,
    normalize_swarm_site,
    sanitize_for_evidence,
)
from app.config.effective import resolve_portainer_expected
from app.config.loader import load_config_dir
from app.config.validation import validate_config
from app.database.repository import Repository
from app.domain import CheckResult, CheckStatus
from app.evidence.manager import EvidenceManager
from app.orchestrator.aggregation import aggregate_status
from app.orchestrator.run_context import RunContext
from app.validators.portainer import PortainerValidator
from app.validators.site_parity import SiteParityValidator


class PortainerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config_dir("tests/fixtures/config_valid")
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

    def test_required_service_exists_and_expected_state_matches(self):
        results = self.validate_sites(self.fixture_sites())
        self.assertIn(CheckStatus.PASS, self.statuses(results, "portainer.service.exists"))
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

    def test_required_service_missing_fails_without_reporting_transport_error(self):
        sites = self.fixture_sites()
        sites["site1"]["services"] = []
        results = self.validate_sites(sites)
        missing = [
            result
            for result in results
            if result.check_id == "portainer.service.exists" and result.site == "site1"
        ][0]
        self.assertEqual(missing.status, CheckStatus.FAIL)
        self.assertEqual(missing.actual["exists"], False)

    def test_desired_running_healthy_and_image_mismatches_fail_separately(self):
        sites = self.fixture_sites()
        service = sites["site1"]["services"][0]
        service["desired_replicas"] = 2
        service["running_replicas"] = 2
        service["healthy_replicas"] = 2
        service["image"] = "example/recording-gateway:wrong"
        results = self.validate_sites(sites)
        self.assertIn(
            CheckStatus.FAIL,
            self.statuses(results, "portainer.service.desired_replicas"),
        )
        self.assertIn(
            CheckStatus.FAIL,
            self.statuses(results, "portainer.service.running_replicas"),
        )
        self.assertIn(
            CheckStatus.FAIL,
            self.statuses(results, "portainer.service.healthy_replicas"),
        )
        self.assertIn(CheckStatus.FAIL, self.statuses(results, "portainer.service.image"))

    def test_health_unavailable_is_actionable_error_not_fabricated_count(self):
        sites = self.fixture_sites()
        service = sites["site1"]["services"][0]
        service["healthy_replicas"] = None
        service["health"] = {
            "available": False,
            "source": "not_available_from_response",
            "definition": "no health signal exposed",
        }
        results = self.validate_sites(sites)
        health = [
            result
            for result in results
            if result.check_id == "portainer.service.healthy_replicas"
            and result.site == "site1"
        ][0]
        self.assertEqual(health.status, CheckStatus.ERROR)
        self.assertIn("health signal unavailable", health.message)

    def test_failed_rejected_and_restarting_tasks_fail_task_state(self):
        for field in ["failed_tasks", "rejected_tasks", "restarting_tasks"]:
            with self.subTest(field=field):
                sites = self.fixture_sites()
                sites["site1"]["services"][0][field] = 1
                results = self.validate_sites(sites)
                task_state = [
                    result
                    for result in results
                    if result.check_id == "portainer.service.task_state"
                    and result.site == "site1"
                ][0]
                self.assertEqual(task_state.status, CheckStatus.FAIL)

    def test_starting_tasks_follow_explicit_policy_and_do_not_default_to_fail(self):
        sites = self.fixture_sites()
        sites["site1"]["services"][0]["starting_tasks"] = 1
        results = self.validate_sites(sites)
        task_state = [
            result
            for result in results
            if result.check_id == "portainer.service.task_state" and result.site == "site1"
        ][0]
        self.assertEqual(task_state.status, CheckStatus.PASS)

        policy_config = copy.deepcopy(self.config)
        policy_config["portainer_expected"]["defaults"]["expected"]["task_state_policy"][
            "starting"
        ] = "WARNING"
        task_state = [
            result
            for result in PortainerValidator().validate(
                {"sites": sites, "errors": []}, policy_config, self.ctx
            )
            if result.check_id == "portainer.service.task_state" and result.site == "site1"
        ][0]
        self.assertEqual(task_state.status, CheckStatus.WARNING)

    def test_common_inventory_resolves_site_overrides_and_optional_absence(self):
        config = copy.deepcopy(self.config)
        config["portainer_expected"]["services"]["optional-worker"] = {
            "name": "optional-worker",
            "required": False,
            "expected": {
                "desired_replicas": 1,
                "running_replicas": 1,
                "healthy_replicas": 1,
                "service_state": "active",
            },
        }
        resolved = resolve_portainer_expected(config)
        self.assertEqual(len(resolved["sites"]["site1"]["services"]), 2)
        self.assertEqual(len(resolved["sites"]["site2"]["services"]), 2)
        results = PortainerValidator().validate(
            {"sites": self.fixture_sites(), "errors": []}, config, self.ctx
        )
        optional = [
            result
            for result in results
            if result.check_id == "portainer.service.exists"
            and result.target == "optional-worker"
        ]
        self.assertEqual([result.status for result in optional], [CheckStatus.SKIPPED] * 2)

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

        client = PortainerClient(_settings(retries=1), transport=httpx.MockTransport(handler))
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
        config["portainer_expected"] = {
            "collection_mode": "live",
            "sites": {
                "site1": {
                    "environment_type": "docker_swarm",
                    "connection": {
                        "url_env": "PORTAINER_SITE1_URL",
                        "endpoint_id": "1",
                        "api_contract": "unknown_contract",
                        "auth": {"type": "x_api_key", "token_env": "PORTAINER_SITE1_TOKEN"},
                        "tls": {"verify": True},
                        "timeouts": {"connect_seconds": 1, "read_seconds": 1},
                        "retries": {"attempts": 0, "backoff_seconds": 0},
                    },
                    "services": resolve_portainer_expected(self.config)["sites"]["site1"][
                        "services"
                    ],
                }
            },
        }
        with patch.dict(
            "os.environ",
            {"PORTAINER_SITE1_URL": "https://portainer.invalid", "PORTAINER_SITE1_TOKEN": "x"},
        ):
            actual = PortainerCollector().collect(
                RunContext(self.ctx.run_id, config, self.ctx.repository, self.ctx.evidence)
            )
        self.assertEqual(actual["errors"][0]["code"], "PORTAINER_UNSUPPORTED_API")

    def test_live_portainer_mode_requires_runtime_values_in_preflight(self):
        config = copy.deepcopy(self.config)
        config["portainer_expected"]["collection_mode"] = "live"
        config["portainer_expected"]["fixture_actual"] = None
        for site_id, site in config["portainer_expected"]["sites"].items():
            site["connection"] = {
                "url_env": f"PORTAINER_{site_id.upper()}_URL",
                "endpoint_id": "1",
                "api_contract": "docker_proxy_v1",
                "auth": {"type": "x_api_key", "token_env": f"PORTAINER_{site_id.upper()}_TOKEN"},
                "tls": {"verify": True},
                "timeouts": {"connect_seconds": 1, "read_seconds": 1},
                "retries": {"attempts": 0, "backoff_seconds": 0},
            }
        with patch.dict("os.environ", {}, clear=True):
            report = validate_config(config)
        self.assertFalse(report.ok)
        self.assertTrue(
            any("Portainer URL runtime value is missing" in line for line in report.lines())
        )

    def test_parity_match_does_not_hide_both_sites_wrong(self):
        sites = self.fixture_sites()
        sites["site1"]["services"][0]["running_replicas"] = 2
        sites["site2"]["services"][0]["running_replicas"] = 2
        results = self.validate_sites(sites)
        parity = SiteParityValidator().validate({"results": results}, self.config, self.ctx)
        running_failures = [
            result
            for result in results
            if result.check_id == "portainer.service.running_replicas"
        ]
        parity_running = [
            result for result in parity if result.check_id == "parity.portainer.running_replicas"
        ][0]
        self.assertEqual(
            [result.status for result in running_failures],
            [CheckStatus.FAIL, CheckStatus.FAIL],
        )
        self.assertEqual(parity_running.status, CheckStatus.PASS)
        self.assertNotEqual(aggregate_status(results + parity, self.config), CheckStatus.PASS)

    def test_parity_mismatch_and_explicit_allowed_difference(self):
        sites = self.fixture_sites()
        sites["site2"]["services"][0]["running_replicas"] = 2
        results = self.validate_sites(sites)
        parity = SiteParityValidator().validate({"results": results}, self.config, self.ctx)
        mismatch = [
            result for result in parity if result.check_id == "parity.portainer.running_replicas"
        ][0]
        self.assertEqual(mismatch.status, CheckStatus.WARNING)
        allowed_config = copy.deepcopy(self.config)
        allowed_config["rules"]["parity"] = [
            {
                "enabled": True,
                "module": "portainer",
                "fields": ["running_replicas"],
                "sites": ["site1", "site2"],
                "mismatch_status": "WARNING",
                "allowed_differences": [
                    {
                        "field": "running_replicas",
                        "target": "recording-gateway",
                        "site_values": {"site1": 3, "site2": 2},
                    }
                ],
            }
        ]
        allowed = SiteParityValidator().validate({"results": results}, allowed_config, self.ctx)[0]
        self.assertEqual(allowed.status, CheckStatus.PASS)
        self.assertTrue(allowed.metadata["allowed_difference"])


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
