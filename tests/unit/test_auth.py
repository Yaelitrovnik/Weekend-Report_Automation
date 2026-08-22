from __future__ import annotations

import os
import time
import unittest
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
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


OIDC_ISSUER = "https://issuer.invalid/"
OIDC_AUDIENCE = "weekend-report"
OIDC_JWKS_URL = "https://issuer.invalid/.well-known/jwks.json"


def _oidc_env(**overrides: str) -> dict[str, str]:
    env = {
        "WEEKEND_REPORT_AUTH_MODE": "production",
        "WEEKEND_REPORT_AUTH_PROVIDER": "oidc",
        "WEEKEND_REPORT_AUTH_OIDC_ISSUER": OIDC_ISSUER,
        "WEEKEND_REPORT_AUTH_OIDC_AUDIENCE": OIDC_AUDIENCE,
        "WEEKEND_REPORT_AUTH_OIDC_JWKS_URL": OIDC_JWKS_URL,
        "WEEKEND_REPORT_AUTHORIZED_REVIEWERS": "alice@example.invalid",
    }
    env.update(overrides)
    return env


class OidcAuthTests(unittest.TestCase):
    """Mirrors the trusted_header coverage in AuthTests, for the oidc provider.

    No network call is ever made: _oidc_signing_key (the only piece of
    app.auth that would talk to a real JWKS endpoint) is patched to return
    our locally generated test key's public half directly. Token
    verification itself (signature, expiry, audience, issuer) still runs
    for real via jwt.decode - only the network-touching key lookup is
    mocked, matching how the trusted_header tests above never hit real
    infrastructure either.
    """

    # Declared here (not just assigned in setUpClass) so mypy recognizes
    # these as real class attributes rather than flagging every reference
    # to self.private_pem/self.public_pem/self.other_private_pem below as
    # attr-defined. setUpClass still does the actual key generation and
    # assignment at test-run time, exactly as before.
    private_pem: bytes
    public_pem: bytes
    other_private_pem: bytes

    @classmethod
    def setUpClass(cls) -> None:
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        cls.public_pem = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.other_private_pem = other_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )

    def _token(self, *, private_pem: bytes | None = None, claims: dict | None = None) -> str:
        now = int(time.time())
        payload = claims or {
            "iss": OIDC_ISSUER,
            "aud": OIDC_AUDIENCE,
            "iat": now,
            "exp": now + 300,
            "email": "alice@example.invalid",
        }
        return jwt.encode(payload, private_pem or self.private_pem, algorithm="RS256")

    def test_valid_oidc_token_is_accepted(self):
        token = self._token()
        with patch.dict(os.environ, _oidc_env()):
            with patch("app.auth._oidc_signing_key", return_value=self.public_pem):
                reviewer = resolve_reviewer(
                    request({"Authorization": f"Bearer {token}"}), mutating=True
                )
        self.assertEqual(reviewer, "alice@example.invalid")

    def test_expired_oidc_token_is_rejected(self):
        now = int(time.time())
        token = self._token(
            claims={
                "iss": OIDC_ISSUER,
                "aud": OIDC_AUDIENCE,
                "iat": now - 7200,
                "exp": now - 3600,
                "email": "alice@example.invalid",
            }
        )
        with patch.dict(os.environ, _oidc_env()):
            with patch("app.auth._oidc_signing_key", return_value=self.public_pem):
                with self.assertRaises(HTTPException) as raised:
                    resolve_reviewer(request({"Authorization": f"Bearer {token}"}))
        self.assertEqual(raised.exception.status_code, 401)

    def test_token_signed_by_untrusted_key_is_rejected(self):
        # The JWKS endpoint correctly returns our trusted public key - but
        # the token was signed by a different private key entirely, so
        # signature verification must fail regardless of what JWKS returns.
        token = self._token(private_pem=self.other_private_pem)
        with patch.dict(os.environ, _oidc_env()):
            with patch("app.auth._oidc_signing_key", return_value=self.public_pem):
                with self.assertRaises(HTTPException) as raised:
                    resolve_reviewer(request({"Authorization": f"Bearer {token}"}))
        self.assertEqual(raised.exception.status_code, 401)

    def test_wrong_audience_is_rejected(self):
        now = int(time.time())
        token = self._token(
            claims={
                "iss": OIDC_ISSUER,
                "aud": "some-other-service",
                "iat": now,
                "exp": now + 300,
                "email": "alice@example.invalid",
            }
        )
        with patch.dict(os.environ, _oidc_env()):
            with patch("app.auth._oidc_signing_key", return_value=self.public_pem):
                with self.assertRaises(HTTPException) as raised:
                    resolve_reviewer(request({"Authorization": f"Bearer {token}"}))
        self.assertEqual(raised.exception.status_code, 401)

    def test_missing_bearer_token_is_rejected(self):
        with patch.dict(os.environ, _oidc_env()):
            with self.assertRaises(HTTPException) as raised:
                resolve_reviewer(request({}))
        self.assertEqual(raised.exception.status_code, 401)

    def test_unauthorized_reviewer_is_rejected_for_oidc(self):
        token = self._token()
        env = _oidc_env(WEEKEND_REPORT_AUTHORIZED_REVIEWERS="someone-else@example.invalid")
        with patch.dict(os.environ, env):
            with patch("app.auth._oidc_signing_key", return_value=self.public_pem):
                with self.assertRaises(HTTPException) as raised:
                    resolve_reviewer(request({"Authorization": f"Bearer {token}"}))
        self.assertIn("not authorized", str(raised.exception))

    def test_missing_oidc_configuration_is_rejected(self):
        env = {
            "WEEKEND_REPORT_AUTH_MODE": "production",
            "WEEKEND_REPORT_AUTH_PROVIDER": "oidc",
        }
        with patch.dict(os.environ, env):
            with self.assertRaises(HTTPException) as raised:
                resolve_reviewer(request({"Authorization": "Bearer whatever"}))
        self.assertEqual(raised.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()