from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException, Request
from starlette.datastructures import Headers

from app.security import csrf_token_for_template, issue_csrf_token, require_csrf_for_mutation


def request(headers: dict[str, str] | None = None) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": Headers(headers or {}).raw,
        }
    )


class SecurityTests(unittest.TestCase):
    def test_csrf_is_optional_when_not_configured_for_local_runs(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(csrf_token_for_template(), "")
            require_csrf_for_mutation(request())

    def test_configured_csrf_token_allows_mutation(self):
        with patch.dict(
            os.environ,
            {"WEEKEND_REPORT_CSRF_SIGNING_KEY": "test-csrf-key"},
            clear=True,
        ):
            token = csrf_token_for_template()
            self.assertTrue(token)
            require_csrf_for_mutation(request({"X-CSRF-Token": token}))

    def test_missing_or_invalid_csrf_token_is_rejected_when_configured(self):
        with patch.dict(
            os.environ,
            {"WEEKEND_REPORT_CSRF_SIGNING_KEY": "test-csrf-key"},
            clear=True,
        ):
            for headers in ({}, {"X-CSRF-Token": "invalid"}):
                with self.subTest(headers=headers):
                    with self.assertRaises(HTTPException) as raised:
                        require_csrf_for_mutation(request(headers))
                    self.assertEqual(raised.exception.status_code, 403)

    def test_expired_csrf_token_is_rejected(self):
        env = {
            "WEEKEND_REPORT_CSRF_SIGNING_KEY": "test-csrf-key",
            "WEEKEND_REPORT_CSRF_TTL_SECONDS": "1",
        }
        with patch.dict(os.environ, env, clear=True):
            with patch("app.security.time.time", return_value=100):
                token = issue_csrf_token()
            with patch("app.security.time.time", return_value=102):
                with self.assertRaises(HTTPException):
                    require_csrf_for_mutation(request({"X-CSRF-Token": token}))


if __name__ == "__main__":
    unittest.main()
