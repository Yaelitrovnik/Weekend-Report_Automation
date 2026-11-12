"""Shared redaction at collection, persistence and logging boundaries."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlsplit, urlunsplit

REDACTED = "<REDACTED>"
SENSITIVE = (
    "password",
    "passwd",
    "secret",
    "token",
    "authorization",
    "apikey",
    "connectionstring",
    "connection",
    "privatekey",
    "signingkey",
    "cookie",
)
ASSIGNMENT = re.compile(
    r"(?i)([\w.-]*(?:password|passwd|secret|token|authorization|api[_-]?key|"
    r"connection[_-]?string|private[_-]?key|signing[_-]?key)[\w.-]*\s*[=:]\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s;&,]+)"
)
PRIVATE_KEY = re.compile(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", re.S)


def sensitive_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", key.lower())
    return any(part in normalized for part in SENSITIVE)


def sanitize_endpoint(value: str) -> str:
    try:
        url = urlsplit(value)
        host = url.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        if url.port:
            host += f":{url.port}"
        return urlunsplit((url.scheme, host, url.path, "", ""))
    except ValueError:
        return "<invalid endpoint>"


def sanitize(value: Any, secrets: Iterable[str | None] = ()) -> Any:
    known = tuple(secret for secret in secrets if secret)
    if isinstance(value, dict):
        return {
            str(key): REDACTED if sensitive_key(str(key)) else sanitize(item, known)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [sanitize(item, known) for item in value]
    if not isinstance(value, str):
        return value
    text = value
    for secret in sorted(known, key=len, reverse=True):
        text = text.replace(secret, REDACTED)
    # Docker Env entries, including connection strings, are entire secret values.
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", text) and sensitive_key(text.split("=", 1)[0]):
        return text.split("=", 1)[0] + "=" + REDACTED
    text = re.sub(
        r"(?im)^(\s*(?:set-cookie|cookie)\s*:\s*)[^\r\n]*", lambda match: match[1] + REDACTED, text
    )
    text = PRIVATE_KEY.sub(REDACTED, text)
    text = re.sub(r"(?i)([a-z][a-z0-9+.-]*://)[^/\s@]+@", r"\1<REDACTED>@", text)
    text = re.sub(r"(?i)\b(Bearer|Basic)\s+[^\s,;]+", r"\1<REDACTED>", text)
    return ASSIGNMENT.sub(lambda match: match[1] + REDACTED, text)


def configured_secrets(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if sensitive_key(str(key)) and isinstance(item, str) and item:
                found.append(item)
            else:
                found.extend(configured_secrets(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(configured_secrets(item))
    return found
