"""LumenX admin API client.

Wraps every endpoint documented in api_desc.txt. Auth header is attached
automatically. All methods return parsed JSON (dict/list) and raise
httpx.HTTPStatusError on non-2xx responses.
"""
from __future__ import annotations

from typing import Any, Optional

import httpx

from config import settings


class LumenXClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        admin_token: Optional[str] = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = (base_url or settings.lumenx_base_url).rstrip("/")
        self.admin_token = admin_token or settings.lumenx_admin_token
        if not self.admin_token:
            raise RuntimeError("LUMENX_ADMIN_TOKEN is not set")
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers={"X-Admin-Token": self.admin_token},
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "LumenXClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ---------- admin: monitoring ----------
    def get_stats(self) -> dict:
        r = self._client.get("/api/admin/stats")
        r.raise_for_status()
        return r.json()

    def get_inbox(self, since: Optional[str] = None) -> dict:
        params = {"since": since} if since else None
        r = self._client.get("/api/admin/inbox", params=params)
        r.raise_for_status()
        return r.json()

    # ---------- admin: threads ----------
    def get_threads(self) -> list[dict]:
        r = self._client.get("/api/admin/threads")
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else data.get("threads", [])

    def get_thread(self, thread_id: str) -> dict:
        r = self._client.get(f"/api/admin/threads/{thread_id}")
        r.raise_for_status()
        return r.json()

    def post_reply(
        self,
        thread_id: str,
        text: str,
        draft_source: str = "agent",
        confidence: Optional[float] = None,
    ) -> dict:
        body: dict[str, Any] = {"text": text, "draft_source": draft_source}
        if confidence is not None:
            body["confidence"] = confidence
        r = self._client.post(f"/api/admin/threads/{thread_id}/reply", json=body)
        r.raise_for_status()
        return r.json()

    def mark_read(self, thread_id: str) -> dict:
        r = self._client.post(f"/api/admin/threads/{thread_id}/mark-read")
        r.raise_for_status()
        return r.json()

    # ---------- admin: data ----------
    def get_export(self) -> dict:
        r = self._client.get("/api/admin/export")
        r.raise_for_status()
        return r.json()

    def get_products(self) -> dict:
        r = self._client.get("/api/admin/products")
        r.raise_for_status()
        return r.json()

    def get_product(self, product_id: str) -> dict:
        r = self._client.get(f"/api/admin/products/{product_id}")
        r.raise_for_status()
        return r.json()
