"""Client for the local game server lifecycle API: GET <base>/<game_id>/<cmd>."""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)


class ServerAPI:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    async def command(self, game_id: str, cmd: str) -> str:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.get(f"{self.base_url}/{game_id}/{cmd}")
            r.raise_for_status()
            return r.text

    async def start(self, game_id: str) -> str:
        log.info("asking server API to start %s", game_id)
        return await self.command(game_id, "start")

    async def stop(self, game_id: str) -> str:
        return await self.command(game_id, "stop")
