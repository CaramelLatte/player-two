"""Skeleton uinput ActionBackend. Implement per-game tool mappings here when the
second game is added; tools stay the same, only their implementation changes."""

from __future__ import annotations

from typing import Any

from actions.base import ActionBackend, ToolResult


class UInputBackend(ActionBackend):
    name = "uinput"
    supported_tools: frozenset[str] = frozenset()  # grows as tools are implemented

    def __init__(self, keymap: dict[str, str] | None = None):
        self.keymap = keymap or {}
        self._device = None  # VirtualInput, created lazily

    async def execute(self, tool: str, args: dict[str, Any]) -> ToolResult:
        return ToolResult(False, error=f"uinput backend does not implement '{tool}' yet")

    async def cancel(self) -> None:
        return None

    async def send_chat(self, message: str) -> ToolResult:
        return ToolResult(False, error="uinput chat not implemented yet")
