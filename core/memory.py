"""Short-term memory: a rolling log of chat and notable events for prompt context."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass


@dataclass
class MemoryEntry:
    time: float
    kind: str  # chat_in | chat_out | event | task
    text: str


class ShortTermMemory:
    def __init__(self, max_entries: int = 40):
        self.entries: deque[MemoryEntry] = deque(maxlen=max_entries)

    def add(self, kind: str, text: str) -> None:
        self.entries.append(MemoryEntry(time.time(), kind, text))

    def chat_in(self, sender: str, text: str, addressed: bool = True) -> None:
        self.add("chat_in", f"<{sender}> {text}" + ("" if addressed else "  (not addressed to you)"))

    def chat_out(self, name: str, text: str) -> None:
        self.add("chat_out", f"<{name}> {text}")

    def render(self, limit: int = 20, now: float | None = None) -> str:
        now = now or time.time()
        items = list(self.entries)[-limit:]
        if not items:
            return "(nothing yet)"
        lines = []
        for e in items:
            ago = int(now - e.time)
            stamp = f"{ago}s ago" if ago < 120 else f"{ago // 60}m ago"
            lines.append(f"[{stamp}] {e.text}")
        return "\n".join(lines)
