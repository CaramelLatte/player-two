"""Async HTTP client for the Node.js Mineflayer bridge."""

from __future__ import annotations

from typing import Any

import httpx


class BridgeError(RuntimeError):
    pass


class BridgeClient:
    def __init__(self, base_url: str, action_timeout_s: float = 120):
        self.base_url = base_url.rstrip("/")
        self.action_timeout_s = action_timeout_s
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=httpx.Timeout(15.0))

    async def close(self) -> None:
        await self._client.aclose()

    async def health(self) -> dict[str, Any]:
        r = await self._client.get("/health", timeout=3)
        r.raise_for_status()
        return r.json()

    async def state(self) -> dict[str, Any]:
        r = await self._client.get("/state")
        if r.status_code == 503:
            raise BridgeError("bot is not connected to the server")
        r.raise_for_status()
        return r.json()

    async def events(self, after: int, wait_s: float = 25) -> tuple[list[dict[str, Any]], int]:
        r = await self._client.get("/events", params={"after": after, "wait": wait_s}, timeout=wait_s + 10)
        r.raise_for_status()
        body = r.json()
        return body["events"], body["last_id"]

    async def action(self, name: str, args: dict[str, Any] | None = None, timeout_s: float | None = None) -> dict[str, Any]:
        timeout = (timeout_s or self.action_timeout_s) + 15
        r = await self._client.post("/action", json={"name": name, "args": args or {}}, timeout=timeout)
        r.raise_for_status()
        return r.json()

    async def stop(self) -> dict[str, Any]:
        r = await self._client.post("/stop", timeout=5)
        r.raise_for_status()
        return r.json()
