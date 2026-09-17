from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import httpx

from app.collectors.doctor import DoctorCollector
from app.database.repository import Repository
from app.evidence.manager import EvidenceManager
from app.orchestrator.run_context import RunContext
from tests.fixtures.config_factory import load_fixture_config


class DoctorCollectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_fixture_config()

        doctor = self.config["doctor"]["doctor"]
        doctor["mode"] = "api"
        doctor["fixture_actual"] = None
        doctor["api"] = {
            "site1_url": "https://doctor-site1.example/doctor-api",
            "site2_url": "https://doctor-site2.example/doctor-api",
        }

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

        self.repo = Repository("sqlite:///:memory:")
        self.addCleanup(self.repo.close)

        self.ctx = RunContext(
            "WR-20260915-000001",
            self.config,
            self.repo,
            EvidenceManager(Path(self.tmp.name)),
        )

    def collect(self, handler) -> dict:
        collector = DoctorCollector(
            transport=httpx.MockTransport(handler),
        )
        return collector.collect(self.ctx)

    def test_live_healthy_services_are_normalized(self) -> None:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.method)

            return httpx.Response(
                200,
                json=[
                    {
                        "id": 1,
                        "name": "service-01",
                        "status": "Healthy",
                        "entries": [
                            {
                                "id": 1,
                                "name": "service-01-health",
                                "status": "Healthy",
                                "description": None,
                            }
                        ],
                    }
                ],
            )

        actual = self.collect(handler)

        self.assertEqual(actual["errors"], [])
        self.assertEqual(set(actual["sites"]), {"site1", "site2"})

        for site_id in ("site1", "site2"):
            service = actual["sites"][site_id]["services"]["service-01"]

            self.assertTrue(service["healthy"])
            self.assertEqual(service["status"], "Healthy")
            self.assertNotIn("reason", service)

        self.assertEqual(calls, ["GET", "GET"])

    def test_live_unhealthy_service_has_reason(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            unhealthy = request.url.host == "doctor-site1.example"

            if unhealthy:
                return httpx.Response(
                    200,
                    json=[
                        {
                            "name": "service-03",
                            "status": "Unhealthy",
                            "entries": [
                                {
                                    "name": "database",
                                    "status": "Unhealthy",
                                    "description": "Database unavailable",
                                }
                            ],
                        }
                    ],
                )

            return httpx.Response(
                200,
                json=[
                    {
                        "name": "service-03",
                        "status": "Healthy",
                        "entries": [
                            {
                                "name": "database",
                                "status": "Healthy",
                                "description": None,
                            }
                        ],
                    }
                ],
            )

        actual = self.collect(handler)

        self.assertEqual(actual["errors"], [])

        unhealthy = actual["sites"]["site1"]["services"]["service-03"]

        self.assertFalse(unhealthy["healthy"])
        self.assertEqual(unhealthy["status"], "Unhealthy")
        self.assertIn("Database unavailable", unhealthy["reason"])

        healthy = actual["sites"]["site2"]["services"]["service-03"]

        self.assertTrue(healthy["healthy"])

    def test_invalid_json_is_collection_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                text="this is not json",
            )

        actual = self.collect(handler)

        self.assertEqual(actual["sites"], {})
        self.assertEqual(len(actual["errors"]), 2)

        self.assertEqual(
            {error["code"] for error in actual["errors"]},
            {"DOCTOR_INVALID_RESPONSE"},
        )

    def test_http_500_is_collection_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                500,
                json={"error": "server failure"},
            )

        actual = self.collect(handler)

        self.assertEqual(actual["sites"], {})
        self.assertEqual(len(actual["errors"]), 2)

        for error in actual["errors"]:
            self.assertEqual(error["code"], "DOCTOR_HTTP_ERROR")
            self.assertEqual(error["status_code"], 500)
            self.assertTrue(error["retryable"])

    def test_timeout_is_collection_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout(
                "request timed out",
                request=request,
            )

        actual = self.collect(handler)

        self.assertEqual(actual["sites"], {})
        self.assertEqual(len(actual["errors"]), 2)

        for error in actual["errors"]:
            self.assertEqual(error["code"], "DOCTOR_TIMEOUT")
            self.assertTrue(error["retryable"])

    def test_empty_api_response_is_no_services_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=[],
            )

        actual = self.collect(handler)

        self.assertEqual(actual["sites"], {})
        self.assertEqual(len(actual["errors"]), 2)

        self.assertEqual(
            {error["code"] for error in actual["errors"]},
            {"DOCTOR_NO_SERVICES_DISCOVERED"},
        )


if __name__ == "__main__":
    unittest.main()