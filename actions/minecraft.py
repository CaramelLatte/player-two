"""Minecraft action backend: forwards tools to the Mineflayer bridge."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from actions.base import ActionBackend, ToolResult
from game_state.minecraft.bridge_client import BridgeClient

log = logging.getLogger(__name__)

# Universal tool name -> bridge action name. Identity for everything we support.
_TOOL_TO_ACTION = {
    name: name
    for name in (
        "move_to_position", "go_to_player", "follow_player", "stop", "go_to_surface",
        "collect_block", "mine_block", "find_blocks", "place_block",
        "craft_item", "smelt_item", "equip_item", "eat", "give_item", "drop_item",
        "attack_entity", "sleep_in_bed",
    )
}


class MineflayerBackend(ActionBackend):
    name = "mineflayer"
    supported_tools = frozenset(_TOOL_TO_ACTION)

    def __init__(self, client: BridgeClient):
        self.client = client

    async def execute(self, tool: str, args: dict[str, Any]) -> ToolResult:
        action = _TOOL_TO_ACTION.get(tool)
        if not action:
            return ToolResult(False, error=f"tool '{tool}' is not available in Minecraft")
        clean = {k: v for k, v in (args or {}).items() if v is not None}
        try:
            resp = await self.client.action(action, clean)
        except httpx.HTTPError as exc:
            return ToolResult(False, error=f"bridge request failed: {exc.__class__.__name__}: {exc}")
        if resp.get("ok"):
            return ToolResult(True, result=resp.get("result"))
        return ToolResult(False, error=resp.get("error") or "unknown error")

    async def cancel(self) -> None:
        try:
            await self.client.stop()
        except httpx.HTTPError as exc:
            log.warning("bridge stop failed: %s", exc)

    async def send_chat(self, message: str) -> ToolResult:
        try:
            resp = await self.client.action("send_chat", {"message": message})
        except httpx.HTTPError as exc:
            return ToolResult(False, error=f"bridge request failed: {exc}")
        return ToolResult(bool(resp.get("ok")), result=resp.get("result"), error=resp.get("error"))
