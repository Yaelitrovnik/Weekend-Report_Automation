from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException, Request
from starlette.datastructures import Headers

from app.auth import issue_csrf_token, require_csrf_for_mutation, resolve_reviewer


def request(headers: dict[str, str] | None = None) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": Headers(headers or {}).raw,
        }
    )


class AuthTests(unittest.TestCase):
    def test_development_allows_x_reviewer(self):
        with patch.dict(os.environ, {"WEEKEND_REPORT_AUTH_MODE": "development"}):
            self.assertEqual(resolve_reviewer(request({"X-Reviewer": "alice"})), "alice")

    def test_production_rejects_x_reviewer(self):
        env = {
            "WEEKEND_REPORT_AUTH_MODE": "production",
            "WEEKEND_REPORT_AUTH_PROVIDER": "trusted_header",
            "WEEKEND_REPORT_AUTH_TRUSTED_HEADER": "X-Authenticated-User",
            "WEEKEND_REPORT_AUTHORIZED_REVIEWERS": "alice",
        }
        with patch.dict(os.environ, env):
            with self.assertRaises(Exception) as raised:
                resolve_reviewer(
                    request({"X-Reviewer": "mallory", "X-Authenticated-User": "alice"}),
                    mutating=True,
                )
            self.assertIn("X-Reviewer", str(raised.exception))

    def test_production_trusted_header_and_csrf(self):
        env = {
            "WEEKEND_REPORT_AUTH_MODE": "production",
            "WEEKEND_REPORT_AUTH_PROVIDER": "trusted_header",
            "WEEKEND_REPORT_AUTH_TRUSTED_HEADER": "X-Authenticated-User",
            "WEEKEND_REPORT_AUTHORIZED_REVIEWERS": "alice",
            "WEEKEND_REPORT_CSRF_SIGNING_KEY": "test-signing-key",
        }
        with patch.dict(os.environ, env):
            token = issue_csrf_token("alice")
            headers = {"X-Authenticated-User": "alice", "X-CSRF-Token": token}
            req = request(headers)
            self.assertEqual(resolve_reviewer(req, mutating=True), "alice")
            require_csrf_for_mutation(req, "alice")

    def test_production_requires_authorized_reviewer_for_reads(self):
        env = {
            "WEEKEND_REPORT_AUTH_MODE": "production",
            "WEEKEND_REPORT_AUTH_PROVIDER": "trusted_header",
            "WEEKEND_REPORT_AUTH_TRUSTED_HEADER": "X-Authenticated-User",
            "WEEKEND_REPORT_AUTHORIZED_REVIEWERS": "alice",
        }
        with patch.dict(os.environ, env):
            with self.assertRaises(Exception) as raised:
                resolve_reviewer(request({"X-Authenticated-User": "mallory"}))
            self.assertIn("not authorized", str(raised.exception))

    def test_csrf_token_is_bound_to_reviewer(self):
        env = {
            "WEEKEND_REPORT_AUTH_MODE": "production",
            "WEEKEND_REPORT_CSRF_SIGNING_KEY": "test-signing-key",
        }
        with patch.dict(os.environ, env):
            token = issue_csrf_token("alice")
            with self.assertRaises(HTTPException):
                require_csrf_for_mutation(request({"X-CSRF-Token": token}), "bob")


if __name__ == "__main__":
    unittest.main()
