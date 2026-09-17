from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any

from fastapi import HTTPException, Request

UNSET_RUNTIME_VALUES = {"", "<TBD>", "<TO_VERIFY>", "UNKNOWN"}
CSRF_HEADER = "X-CSRF-Token"
CSRF_TTL_SECONDS = 3600


def require_csrf_for_mutation(request: Request) -> None:
    """Require a valid CSRF token when CSRF protection is configured.

    Local/test runs may omit WEEKEND_REPORT_CSRF_SIGNING_KEY. Production
    preflight requires the key, so deployed browser mutations are protected.
    """

    key = _configured_csrf_signing_key()
    if key is None:
        return

    token = request.headers.get(CSRF_HEADER, "").strip()
    if not token or not _valid_csrf_token(token, key):
        raise HTTPException(status_code=403, detail="missing or invalid CSRF token")


def csrf_token_for_template() -> str:
    key = _configured_csrf_signing_key()
    if key is None:
        return ""
    return issue_csrf_token(key)


def issue_csrf_token(signing_key: str | None = None) -> str:
    key = signing_key or _required_csrf_signing_key()
    payload = {
        "iat": int(time.time()),
        "nonce": secrets.token_urlsafe(18),
    }
    payload_b64 = _b64encode_json(payload)
    signature = hmac.new(
        key.encode("utf-8"),
        payload_b64.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload_b64}.{signature}"


def _configured_csrf_signing_key() -> str | None:
    key = os.getenv("WEEKEND_REPORT_CSRF_SIGNING_KEY", "").strip()
    if _is_unset(key):
        return None
    return key


def _required_csrf_signing_key() -> str:
    key = _configured_csrf_signing_key()
    if key is None:
        raise HTTPException(
            status_code=503,
            detail="WEEKEND_REPORT_CSRF_SIGNING_KEY is required",
        )
    return key


def _valid_csrf_token(token: str, signing_key: str) -> bool:
    try:
        payload_b64, signature = token.split(".", 1)
    except ValueError:
        return False

    expected = hmac.new(
        signing_key.encode("utf-8"),
        payload_b64.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return False

    try:
        payload = _b64decode_json(payload_b64)
    except (ValueError, json.JSONDecodeError):
        return False

    issued_at = payload.get("iat")
    if not isinstance(issued_at, int):
        return False

    age = int(time.time()) - issued_at
    if age < 0:
        return False
    return age <= _csrf_ttl_seconds()


def _csrf_ttl_seconds() -> int:
    raw = os.getenv(
        "WEEKEND_REPORT_CSRF_TTL_SECONDS",
        str(CSRF_TTL_SECONDS),
    ).strip()
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


def _is_unset(value: str | None) -> bool:
    return value is None or value.strip() in UNSET_RUNTIME_VALUES


def _b64encode_json(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode_json(value: str) -> dict[str, Any]:
    padded = value + "=" * (-len(value) % 4)
    decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
    payload = json.loads(decoded)
    if not isinstance(payload, dict):
        raise ValueError("token payload must be an object")
    return payload
