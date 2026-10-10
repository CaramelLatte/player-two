"""Tool registry: which tools are offered to the LLM for the active game."""

from __future__ import annotations

from typing import Any

from actions.base import ActionBackend
from actions.tools import CORE_TOOLS, TOOLS, ToolSpec


class ToolRegistry:
    def __init__(self, backend: ActionBackend):
        self.backend = backend
        self.specs: list[ToolSpec] = [
            t for t in TOOLS if t.core or t.name in backend.supported_tools
        ]
        self._names = {t.name for t in self.specs}

    def openai_tools(self, exclude: set[str] | None = None) -> list[dict[str, Any]]:
        exclude = exclude or set()
        return [t.to_openai() for t in self.specs if t.name not in exclude]

    def has(self, name: str) -> bool:
        return name in self._names

    def is_core(self, name: str) -> bool:
        return name in CORE_TOOLS
