from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import httpx

from app.collectors.doctor import DoctorCollector
from app.collectors.infrastructure import InfrastructureCollector
from app.collectors.portainer import PortainerCollector
from app.collectors.rabbitmq import RabbitMQCollector
from app.config.loader import ConfigError, load_config_dir
from app.config.validation import validate_config
from app.database.repository import Repository
from app.domain import CheckStatus
from app.evidence.manager import EvidenceManager
from app.executors.ssh import SSHExecutor, SSHResult
from app.orchestrator.aggregation import aggregate_status
from app.orchestrator.execution_plan import parity_applicable
from app.orchestrator.run_context import RunContext
from app.orchestrator.runner import COLLECTORS, VALIDATORS
from app.validators.doctor import DoctorValidator
from app.validators.site_parity import SiteParityValidator
from tests.fixtures.dynamic_sites import dynamic_config, dynamic_env


class DynamicSitesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Repository("sqlite:///:memory:")
        self.addCleanup(self.repo.close)

    def context(self, config):
        return RunContext(
            "WR-20261019-000001", config, self.repo, EvidenceManager(Path(self.tmp.name))
        )

    def validate_modules(self, config):
        ctx = self.context(config)
        results = []
        for module in COLLECTORS:
            actual = COLLECTORS[module]().collect(ctx)
            results.extend(VALIDATORS[module]().validate(actual, config, ctx))
        return results

    def parity(self, config, results=None):
        if results is None:
            results = self.validate_modules(config)
        return SiteParityValidator().validate({"results": results}, config, self.context(config))

    def test_healthy_inventory_one_two_three_and_five_sites(self):
        for count in (1, 2, 3, 5):
            with self.subTest(count=count):
                config = dynamic_config(count)
                report = validate_config(config, production_preflight=False)
                self.assertTrue(report.ok, report.lines())
                sites = [f"region-{index}" for index in range(1, count + 1)]
                self.assertEqual(list(config["doctor"]["doctor"]["api"]["sites"]), sites)
                results = self.validate_modules(config)
                self.assertTrue(all(result.status == CheckStatus.PASS for result in results))
                for module in COLLECTORS:
                    self.assertEqual(
                        {r.site for r in results if r.module == module and r.site}, set(sites)
                    )
                parity = self.parity(config, results)
                self.assertEqual(parity_applicable(config), count > 1)
                self.assertEqual(len(parity), 7 if count > 1 else 0)
                self.assertTrue(all(r.status == CheckStatus.PASS for r in parity))
                for result in parity:
                    self.assertEqual(result.actual["sites"], sites)
                    self.assertEqual(set(result.actual["values"]), set(sites))
                self.assertEqual(aggregate_status(results + parity, config), CheckStatus.PASS)

    def test_third_site_service_presence_image_and_doctor_mismatches(self):
        for change in ("presence", "image", "doctor"):
            with self.subTest(change=change):
                config = dynamic_config(3)
                services = config["portainer_expected"]["fixture_actual"]["sites"]["region-3"][
                    "services"
                ]
                if change == "presence":
                    services.pop()
                    check_id, target = "parity.portainer.service_presence", "second-service"
                elif change == "image":
                    services[0]["image"] = "api:v4"
                    check_id, target = "parity.portainer.image", services[0]["name"]
                else:
                    del config["doctor"]["doctor"]["fixture_actual"]["sites"]["region-3"][
                        "services"
                    ]["service-01"]
                    check_id, target = "parity.doctor.service_presence", "service-01"
                parity = [r for r in self.parity(config) if r.check_id == check_id]
                self.assertEqual(len(parity), 1)
                self.assertEqual(parity[0].status, CheckStatus.FAIL)
                mismatch = parity[0].actual["mismatches"][target]
                self.assertEqual(set(mismatch), {"region-1", "region-2", "region-3"})
                self.assertNotEqual(mismatch["region-1"], mismatch["region-3"])

    def test_independent_failures_survive_matching_parity(self):
        for unhealthy_sites in (("region-3",), ("region-1", "region-2", "region-3")):
            with self.subTest(sites=unhealthy_sites):
                config = dynamic_config(3)
                for site in unhealthy_sites:
                    config["portainer_expected"]["fixture_actual"]["sites"][site]["services"][0][
                        "running_replicas"
                    ] = 0
                    config["doctor"]["doctor"]["fixture_actual"]["sites"][site]["services"][
                        "service-01"
                    ] = {"healthy": False, "reason": "database unavailable"}
                results = self.validate_modules(config)
                parity = self.parity(config, results)
                self.assertNotEqual(aggregate_status(results + parity, config), CheckStatus.PASS)
                for site in unhealthy_sites:
                    self.assertTrue(
                        any(r.site == site and r.status == CheckStatus.FAIL for r in results)
                    )
                self.assertTrue(
                    any(
                        r.check_id == "doctor.module_status"
                        and r.status == CheckStatus.MANUAL_REVIEW
                        for r in results
                    )
                )
                if len(unhealthy_sites) == 3:
                    self.assertTrue(all(r.status == CheckStatus.PASS for r in parity))

    def test_allowed_differences_target_and_full_map_cover_all_sites(self):
        for count in (2, 3):
            for targeted in (True, False):
                with self.subTest(count=count, targeted=targeted):
                    config = dynamic_config(count)
                    observed = config["portainer_expected"]["fixture_actual"]["sites"]
                    target = observed["region-1"]["services"][0]["name"]
                    site_values = {}
                    for index, (site, value) in enumerate(observed.items()):
                        value["services"][0]["image"] = f"api:v{index}"
                        site_values[site] = (
                            value["services"][0]["image"]
                            if targeted
                            else {s["name"]: s["image"] for s in value["services"]}
                        )
                    exception = {"field": "image", "site_values": site_values}
                    if targeted:
                        exception["target"] = target
                    config["rules"]["parity"][0]["allowed_differences"] = [exception]
                    image = next(r for r in self.parity(config) if r.check_id.endswith(".image"))
                    self.assertEqual(image.status, CheckStatus.PASS)
                    self.assertTrue(image.actual["allowed_difference"])
                    # A target exception must never suppress another service's mismatch.
                    observed[f"region-{count}"]["services"][1]["image"] = "unexpected:v9"
                    image = next(r for r in self.parity(config) if r.check_id.endswith(".image"))
                    self.assertEqual(image.status, CheckStatus.FAIL)

    def test_incomplete_exception_and_unreliable_values_do_not_pass(self):
        config = dynamic_config(3)
        sites = config["portainer_expected"]["fixture_actual"]["sites"]
        target = sites["region-1"]["services"][0]["name"]
        sites["region-3"]["services"][0]["image"] = "different:v2"
        config["rules"]["parity"][0]["allowed_differences"] = [
            {
                "field": "image",
                "target": target,
                "site_values": {
                    site: sites[site]["services"][0]["image"] for site in ("region-1", "region-2")
                },
            }
        ]
        image = next(r for r in self.parity(config) if r.check_id.endswith(".image"))
        self.assertEqual(image.status, CheckStatus.FAIL)
        for site in sites.values():
            site["services"][0]["image"] = None
        image = next(r for r in self.parity(config) if r.check_id.endswith(".image"))
        self.assertEqual(image.status, CheckStatus.ERROR)
        self.assertEqual(image.actual["missing_sites"], list(sites))

    def test_invalid_numbered_environment_is_rejected(self):
        for key, value in (
            ("SITE_COUNT", "0"),
            ("SITE_COUNT", "-1"),
            ("SITE_COUNT", "three"),
            ("SITE0_ID", "bad"),
            ("SITE01_ID", "bad"),
            ("SITE_1_ID", "bad"),
            ("SITE4_ID", "extra"),
            ("DOCTOR_SITE4_API_URL", "extra"),
            ("DOCTOR_SITE3_AP_URL", "typo"),
            ("RABBITMQ_SITEX_URL", "bad"),
            ("SITE3_SERVER_0_ID", "bad"),
            ("SITE3_SERVER_2_ID", "extra"),
        ):
            with self.subTest(key=key, value=value):
                env = dynamic_env(3)
                env[key] = value
                with self.assertRaises(ConfigError):
                    load_config_dir("deploy/docker/config", env=env)

    def test_duplicate_empty_missing_and_unknown_sites_are_rejected(self):
        for key, value in (
            ("SITE3_ID", "region-1"),
            ("SITE3_ID", ""),
            ("SITE3_ID", "   "),
            ("DOCTOR_SITE3_API_URL", ""),
            ("SITE3_SERVER_1_HOST", ""),
        ):
            with self.subTest(key=key, value=value):
                env = dynamic_env(3)
                env[key] = value
                report = validate_config(
                    load_config_dir("deploy/docker/config", env=env), production_preflight=False
                )
                self.assertFalse(report.ok)
        for key in ("PORTAINER_SITE3_TOKEN", "RABBITMQ_SITE3_PASSWORD", "SITE3_ID"):
            env = dynamic_env(3)
            del env[key]
            report = validate_config(load_config_dir("deploy/docker/config", env=env))
            self.assertTrue(
                any(
                    key.split("_SITE")[0].lower() in line or "site id" in line
                    for line in report.lines()
                    if "ERROR" in line
                ),
                report.lines(),
            )
        config = dynamic_config(3)
        config["rules"]["parity"][0]["allowed_differences"] = [
            {"field": "image", "site_values": {"unknown-region": "api:v1"}}
        ]
        report = validate_config(config, production_preflight=False)
        self.assertTrue(any("unknown runtime site reference" in line for line in report.lines()))

    def test_live_doctor_collects_every_endpoint_in_runtime_order(self):
        for count in (1, 2, 3):
            config = dynamic_config(count)
            del config["doctor"]["doctor"]["fixture_actual"]
            calls = []

            def handler(request, calls=calls):
                calls.append(str(request.url))
                return httpx.Response(200, json=[{"name": "api", "status": "Healthy"}])

            ctx = self.context(config)
            actual = DoctorCollector(httpx.MockTransport(handler)).collect(ctx)
            self.assertEqual(calls, list(config["doctor"]["doctor"]["api"]["sites"].values()))
            results = DoctorValidator().validate(actual, config, ctx)
            self.assertTrue(all(r.status == CheckStatus.PASS for r in results))

    def test_doctor_site_errors_leave_other_sites_independently_validated(self):
        for error in ("auth", "timeout", "schema", "missing_url"):
            with self.subTest(error=error):
                config = dynamic_config(3)
                del config["doctor"]["doctor"]["fixture_actual"]
                if error == "missing_url":
                    del config["doctor"]["doctor"]["api"]["sites"]["region-3"]

                def handler(request, error=error):
                    if "site3" in request.url.host:
                        if error == "auth":
                            return httpx.Response(401)
                        if error == "timeout":
                            raise httpx.ReadTimeout("timeout", request=request)
                        return httpx.Response(200, json={"invalid": True})
                    return httpx.Response(200, json=[{"name": "api", "status": 2}])

                ctx = self.context(config)
                actual = DoctorCollector(httpx.MockTransport(handler)).collect(ctx)
                self.assertEqual([e["site"] for e in actual["errors"]], ["region-3"])
                results = DoctorValidator().validate(actual, config, ctx)
                self.assertEqual(
                    {r.site for r in results if r.check_id == "doctor.service.health"},
                    {"region-1", "region-2"},
                )
                self.assertEqual(aggregate_status(results, config), CheckStatus.ERROR)
                parity = self.parity(config, results)
                self.assertEqual(parity[0].status, CheckStatus.ERROR)
                self.assertEqual(parity[0].actual["missing_sites"], ["region-3"])

    def test_live_portainer_rabbitmq_and_ssh_iterate_runtime_sites(self):
        for count in (1, 3):
            with self.subTest(count=count):
                config = dynamic_config(count)
                for area in ("portainer_expected", "rabbitmq_expected", "servers"):
                    del config[area]["fixture_actual"]
                    if area != "servers":
                        config[area]["collection_mode"] = "live"
                calls = []

                def handler(request, calls=calls):
                    calls.append((request.url.host, request.url.path))
                    if request.url.path == "/api/status":
                        return httpx.Response(200, json={"Version": "test"})
                    if request.url.path.endswith("/services"):
                        return httpx.Response(
                            200,
                            json=[
                                {
                                    "ID": "api",
                                    "Spec": {
                                        "Name": "api",
                                        "Mode": {"Replicated": {"Replicas": 1}},
                                        "TaskTemplate": {"ContainerSpec": {"Image": "api:v1"}},
                                    },
                                }
                            ],
                        )
                    if request.url.path.endswith("/tasks"):
                        return httpx.Response(
                            200,
                            json=[
                                {
                                    "ID": "task",
                                    "ServiceID": "api",
                                    "DesiredState": "running",
                                    "Status": {
                                        "State": "running",
                                        "ContainerStatus": {"Health": {"Status": "healthy"}},
                                    },
                                }
                            ],
                        )
                    if request.url.path == "/api/queues":
                        return httpx.Response(
                            200,
                            json=[
                                {
                                    "name": "queue",
                                    "vhost": "/",
                                    "messages_ready": 0,
                                    "messages_unacknowledged": 0,
                                    "messages": 0,
                                }
                            ],
                        )
                    if request.url.path == "/api/nodes":
                        return httpx.Response(
                            200,
                            json=[
                                {
                                    "name": "rabbit",
                                    "fd_used": 1,
                                    "fd_total": 1000,
                                    "sockets_used": 1,
                                    "sockets_total": 1000,
                                    "proc_used": 1,
                                    "proc_total": 1000,
                                    "disk_free_alarm": False,
                                }
                            ],
                        )
                    self.fail(f"unexpected request: {request.url}")

                ctx = self.context(config)
                for module, collector in (
                    ("portainer", PortainerCollector),
                    ("rabbitmq", RabbitMQCollector),
                ):
                    actual = collector(httpx.MockTransport(handler)).collect(ctx)
                    self.assertEqual(actual["errors"], [])
                    self.assertEqual(len(actual["sites"]), count)
                    results = VALIDATORS[module]().validate(actual, config, ctx)
                    self.assertTrue(all(r.status == CheckStatus.PASS for r in results), results)
                self.assertEqual(len({host for host, _ in calls}), count * 2)
                ssh = Mock(spec=SSHExecutor)
                outputs = {
                    "df -h | grep mapper": "/dev/mapper/vg-data 1G 0 1G 20% /data",
                    "timedatectl show -p Timezone --value": "UTC",
                    "chronyc tracking": "System time : 0.001 seconds slow of NTP time\n"
                    "Leap status : Normal",
                    "chronyc sources -n": "^* fixture-ntp 1 6 377 12 +10us",
                }
                ssh.run.side_effect = lambda outputs=outputs, **kw: SSHResult(
                    kw["host"], kw["command"], 0, outputs[kw["command"]], "", 0
                )
                actual = InfrastructureCollector(ssh).collect(ctx)
                results = VALIDATORS["infrastructure"]().validate(actual, config, ctx)
                self.assertEqual(len(actual["sites"]), count)
                self.assertTrue(all(r.status == CheckStatus.PASS for r in results), results)
                self.assertEqual(ssh.run.call_count, count * 4)
                for call in ssh.run.call_args_list:
                    self.assertTrue(call.kwargs["password"])
                    self.assertTrue(call.kwargs["known_hosts_path"])

    def test_per_site_server_counts_are_independent(self):
        env = dynamic_env(3)
        env["SITE3_SERVER_COUNT"] = "2"
        env.update({"SITE3_SERVER_2_ID": "extra", "SITE3_SERVER_2_HOST": "extra-host"})
        config = load_config_dir("deploy/docker/config", env=env)
        self.assertEqual(
            [len(s["servers"]) for s in config["servers"]["sites"].values()], [1, 1, 2]
        )

    def test_numbered_variables_are_mapped_by_runtime_id_not_literal_site_name(self):
        env = dynamic_env(3)
        env["SITE1_ID"], env["SITE3_ID"] = "west", "east"
        config = load_config_dir("deploy/docker/config", env=env)
        self.assertEqual(
            list(config["doctor"]["doctor"]["api"]["sites"]), ["west", "region-2", "east"]
        )
        self.assertEqual(
            config["doctor"]["doctor"]["api"]["sites"]["east"], env["DOCTOR_SITE3_API_URL"]
        )
