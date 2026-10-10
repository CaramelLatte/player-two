"""Action backend interface: executes high-level tools for one game."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class ToolResult:
    ok: bool
    result: Any = None
    error: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {"ok": True, "result": self.result} if self.ok else {"ok": False, "error": self.error}


class ActionBackend(ABC):
    """Game-specific implementation of the universal tool set."""

    name: str = "base"
    #: Tool names (from actions.tools) this backend implements.
    supported_tools: frozenset[str] = frozenset()

    @abstractmethod
    async def execute(self, tool: str, args: dict[str, Any]) -> ToolResult:
        ...

    @abstractmethod
    async def cancel(self) -> None:
        """Abort whatever is running (pathing, digging, ...)."""

    @abstractmethod
    async def send_chat(self, message: str) -> ToolResult:
        ...
