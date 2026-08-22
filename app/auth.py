from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any

import jwt
from fastapi import HTTPException, Request

UNSET_RUNTIME_VALUES = {"", "<TBD>", "<TO_VERIFY>", "UNKNOWN"}
CSRF_HEADER = "X-CSRF-Token"
CSRF_TTL_SECONDS = 3600

# Restricted to RS256 (asymmetric) only: accepting a broader algorithm list,
# especially any symmetric algorithm (HS256) or "none", is the classic JWT
# "algorithm confusion" vulnerability class. Extend this list deliberately
# and only for algorithms the configured identity provider is known to use.
OIDC_SUPPORTED_ALGORITHMS = ["RS256"]


def resolve_reviewer(request: Request, *, mutating: bool = False) -> str:
    mode = os.getenv("WEEKEND_REPORT_AUTH_MODE", "development").strip().lower()
    if mode in {"development", "dev", "local"}:
        return (
            request.headers.get("X-Reviewer")
            or os.getenv("WEEKEND_REPORT_DEV_REVIEWER", "anonymous")
            or "anonymous"
        )
    if mode != "production":
        raise HTTPException(status_code=503, detail=f"unsupported auth mode: {mode}")

    if request.headers.get("X-Reviewer"):
        raise HTTPException(
            status_code=400,
            detail="X-Reviewer is development-only and is rejected in production auth mode",
        )

    provider = os.getenv("WEEKEND_REPORT_AUTH_PROVIDER", "").strip()
    if _is_unset(provider):
        raise HTTPException(
            status_code=503,
            detail="WEEKEND_REPORT_AUTH_PROVIDER is required in production auth mode",
        )

    reviewer = _reviewer_from_provider(request, provider)
    _enforce_authorized_reviewer(reviewer)
    return reviewer


def require_csrf_for_mutation(request: Request, reviewer: str | None = None) -> None:
    mode = os.getenv("WEEKEND_REPORT_AUTH_MODE", "development").strip().lower()
    if mode in {"development", "dev", "local"}:
        return
    if not reviewer:
        raise HTTPException(status_code=503, detail="reviewer is required for CSRF validation")
    token = request.headers.get(CSRF_HEADER, "").strip()
    if not token or not _valid_csrf_token(token, reviewer):
        raise HTTPException(status_code=403, detail="missing or invalid CSRF token")


def csrf_token_for_template(reviewer: str | None = None) -> str:
    mode = os.getenv("WEEKEND_REPORT_AUTH_MODE", "development").strip().lower()
    if mode in {"development", "dev", "local"}:
        return ""
    if not reviewer:
        raise HTTPException(status_code=503, detail="reviewer is required to issue CSRF token")
    return issue_csrf_token(reviewer)


def issue_csrf_token(reviewer: str) -> str:
    payload = {
        "reviewer": reviewer,
        "iat": int(time.time()),
        "nonce": secrets.token_urlsafe(18),
    }
    payload_b64 = _b64encode_json(payload)
    signature = hmac.new(
        _csrf_signing_key().encode("utf-8"),
        payload_b64.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload_b64}.{signature}"


def _reviewer_from_provider(request: Request, provider: str) -> str:
    if provider == "trusted_header":
        header_name = os.getenv("WEEKEND_REPORT_AUTH_TRUSTED_HEADER", "").strip()
        if _is_unset(header_name):
            raise HTTPException(
                status_code=503,
                detail="WEEKEND_REPORT_AUTH_TRUSTED_HEADER is required for trusted_header auth",
            )
        reviewer = request.headers.get(header_name)
        if not reviewer:
            raise HTTPException(status_code=401, detail="reviewer identity header is missing")
        return reviewer
    if provider == "oidc":
        return _reviewer_from_oidc(request)
    raise HTTPException(
        status_code=503,
        detail=f"auth provider {provider!r} is not implemented; configure an approved provider",
    )


def _reviewer_from_oidc(request: Request) -> str:
    issuer = os.getenv("WEEKEND_REPORT_AUTH_OIDC_ISSUER", "").strip()
    audience = os.getenv("WEEKEND_REPORT_AUTH_OIDC_AUDIENCE", "").strip()
    jwks_url = os.getenv("WEEKEND_REPORT_AUTH_OIDC_JWKS_URL", "").strip()
    reviewer_claim = (
        os.getenv("WEEKEND_REPORT_AUTH_OIDC_REVIEWER_CLAIM", "email").strip() or "email"
    )
    for label, value in (
        ("WEEKEND_REPORT_AUTH_OIDC_ISSUER", issuer),
        ("WEEKEND_REPORT_AUTH_OIDC_AUDIENCE", audience),
        ("WEEKEND_REPORT_AUTH_OIDC_JWKS_URL", jwks_url),
    ):
        if _is_unset(value):
            raise HTTPException(
                status_code=503,
                detail=f"{label} is required for oidc auth",
            )

    token = _bearer_token(request)
    if token is None:
        raise HTTPException(status_code=401, detail="OIDC bearer token is missing")

    claims = _decode_oidc_token(token, issuer=issuer, audience=audience, jwks_url=jwks_url)

    reviewer = claims.get(reviewer_claim)
    if not isinstance(reviewer, str) or not reviewer:
        raise HTTPException(
            status_code=401,
            detail=f"OIDC token is missing required claim: {reviewer_claim!r}",
        )
    return reviewer


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def _decode_oidc_token(
    token: str, *, issuer: str, audience: str, jwks_url: str
) -> dict[str, Any]:
    signing_key = _oidc_signing_key(jwks_url, token)
    try:
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=OIDC_SUPPORTED_ALGORITHMS,
            audience=audience,
            issuer=issuer,
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail=f"invalid OIDC token: {exc}") from exc
    if not isinstance(claims, dict):
        raise HTTPException(status_code=401, detail="OIDC token payload was not an object")
    return claims


def _oidc_signing_key(jwks_url: str, token: str) -> Any:
    """Resolve the public key used to verify `token`, from the configured JWKS endpoint.

    Isolated in its own function so tests can substitute a known key (see
    tests/unit/test_auth.py) instead of making a real network call to a JWKS
    endpoint. jwt.PyJWKClient handles fetching, caching, and kid-based key
    selection rather than hand-rolling JWKS parsing here.
    """
    try:
        client = jwt.PyJWKClient(jwks_url)
        return client.get_signing_key_from_jwt(token).key
    except jwt.PyJWKClientError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"unable to resolve OIDC signing key from {jwks_url}: {exc}",
        ) from exc


def _enforce_authorized_reviewer(reviewer: str) -> None:
    configured = os.getenv("WEEKEND_REPORT_AUTHORIZED_REVIEWERS", "").strip()
    if _is_unset(configured):
        raise HTTPException(
            status_code=503,
            detail="WEEKEND_REPORT_AUTHORIZED_REVIEWERS is required for production access",
        )
    allowed = {item.strip() for item in configured.split(",") if item.strip()}
    if "*" not in allowed and reviewer not in allowed:
        raise HTTPException(status_code=403, detail="reviewer is not authorized")


def _is_unset(value: str | None) -> bool:
    return value is None or value.strip() in UNSET_RUNTIME_VALUES


def _csrf_signing_key() -> str:
    key = os.getenv("WEEKEND_REPORT_CSRF_SIGNING_KEY", "").strip()
    if _is_unset(key):
        raise HTTPException(
            status_code=503,
            detail="WEEKEND_REPORT_CSRF_SIGNING_KEY is required for production browser mutations",
        )
    return key


def _valid_csrf_token(token: str, reviewer: str) -> bool:
    try:
        payload_b64, signature = token.split(".", 1)
    except ValueError:
        return False
    expected = hmac.new(
        _csrf_signing_key().encode("utf-8"),
        payload_b64.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return False
    try:
        payload = _b64decode_json(payload_b64)
    except (ValueError, json.JSONDecodeError):
        return False
    if payload.get("reviewer") != reviewer:
        return False
    issued_at = payload.get("iat")
    if not isinstance(issued_at, int):
        return False
    return int(time.time()) - issued_at <= _csrf_ttl_seconds()


def _csrf_ttl_seconds() -> int:
    raw = os.getenv("WEEKEND_REPORT_CSRF_TTL_SECONDS", str(CSRF_TTL_SECONDS)).strip()
    try:
        ttl = int(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=503,
            detail="WEEKEND_REPORT_CSRF_TTL_SECONDS must be an integer",
        ) from exc
    if ttl <= 0:
        raise HTTPException(
            status_code=503,
            detail="WEEKEND_REPORT_CSRF_TTL_SECONDS must be greater than zero",
        )
    return ttl


def _b64encode_json(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode_json(value: str) -> dict[str, Any]:
    padded = value + "=" * (-len(value) % 4)
    decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
    payload = json.loads(decoded)
    if not isinstance(payload, dict):
        raise ValueError("CSRF payload must be an object")
    return payload