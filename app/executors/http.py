from __future__ import annotations

import httpx


class HttpExecutor:
    def __init__(self, timeout: float = 10.0, verify_tls: bool = True) -> None:
        self.timeout = timeout
        self.verify_tls = verify_tls

    def get_json(self, url: str, headers: dict[str, str] | None = None) -> dict:
        with httpx.Client(timeout=self.timeout, verify=self.verify_tls) as client:
            response = client.get(url, headers=headers)
            response.raise_for_status()
            return response.json()
