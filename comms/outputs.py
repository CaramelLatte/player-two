"""Output channels for the agent's speech. Text chat today; Voxta voice later.

Add a channel by implementing `async say(text)` and registering it with
OutputRouter. The Voxta channel will live alongside GameChatOutput.
"""

from __future__ import annotations

import logging
from typing import Protocol

from actions.base import ActionBackend, ToolResult

log = logging.getLogger(__name__)


class OutputChannel(Protocol):
    name: str

    async def say(self, text: str) -> ToolResult: ...


class GameChatOutput:
    name = "game_chat"

    def __init__(self, backend: ActionBackend):
        self.backend = backend

    async def say(self, text: str) -> ToolResult:
        return await self.backend.send_chat(text)


class OutputRouter:
    def __init__(self, channels: list[OutputChannel]):
        self.channels = channels

    async def say(self, text: str) -> ToolResult:
        """Send to every channel; the first channel's result is authoritative."""
        primary: ToolResult | None = None
        for ch in self.channels:
            try:
                res = await ch.say(text)
            except Exception as exc:  # noqa: BLE001
                res = ToolResult(False, error=str(exc))
            if not res.ok:
                log.warning("output channel %s failed: %s", ch.name, res.error)
            primary = primary or res
        return primary or ToolResult(False, error="no output channels")
