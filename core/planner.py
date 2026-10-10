"""Autonomous goal selection (LLM-driven, with a deterministic fallback)."""

from __future__ import annotations

import json
import logging
import re
from collections import deque
from typing import Any

from core import prompts
from core.llm import LLMClient

log = logging.getLogger(__name__)

FALLBACK_GOALS = [
    "Collect 8 logs and turn some into planks and sticks.",
    "Make sure you have a wooden pickaxe, then upgrade to stone tools.",
    "Gather 16 cobblestone.",
    "Find some food: hunt a nearby animal or two.",
]


class GoalPlanner:
    def __init__(self, llm: LLMClient, *, persona: str, name: str, game: str, owner: str | None,
                 model: str | None = None):
        self.llm = llm
        self.model = model
        self.persona = persona
        self.name = name
        self.game = game
        self.owner = owner or "your friend"
        self.recent: deque[str] = deque(maxlen=6)
        self._fallback_idx = 0

    def record_outcome(self, goal: str, outcome: str) -> None:
        self.recent.append(f"{goal} -> {outcome}")

    async def next_goal(self, state_text: str, memory_text: str) -> str:
        messages = [
            {"role": "system", "content": prompts.PLANNER_SYSTEM.format(
                persona=self.persona, name=self.name, game=self.game, owner=self.owner)},
            {"role": "user", "content": prompts.PLANNER_USER.format(
                state=state_text, memory=memory_text,
                recent_goals="\n".join(self.recent) or "(none yet)")},
        ]
        try:
            resp = await self.llm.chat(messages, model=self.model, max_tokens=1024, temperature=0.7)
            goal = parse_goal(resp.content)
            if goal:
                return goal
            log.warning("planner returned no usable goal: %r", resp.content[:200])
        except Exception as exc:  # noqa: BLE001
            log.warning("planner failed: %s", exc)
        goal = FALLBACK_GOALS[self._fallback_idx % len(FALLBACK_GOALS)]
        self._fallback_idx += 1
        return goal


def parse_goal(text: str) -> str | None:
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            data: Any = json.loads(m.group(0))
            goal = str(data.get("goal", "")).strip()
            if goal:
                return goal
        except (json.JSONDecodeError, AttributeError):
            pass
    # Fall back to the first non-empty line of plain text.
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    return line[:300] or None
