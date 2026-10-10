"""Decide whether a chat message is addressed to the agent, and de-duplicate."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Callable

from comms.messages import ChatMessage

_YOU = re.compile(r"\b(you|u|ya|your|ur|yours|you're|youre|yourself|you'll|you've|can you|could you)\b", re.I)


@dataclass
class AddressDecision:
    addressed: bool
    reason: str


class Deduplicator:
    """Drops repeats of the same (sender, text) within a window — e.g. the same
    line arriving from both the container log and the in-game client."""

    def __init__(self, window_s: float = 6.0):
        self.window_s = window_s
        self._seen: dict[tuple[str, str], float] = {}

    def is_duplicate(self, msg: ChatMessage, now: float | None = None) -> bool:
        now = now if now is not None else time.time()
        key = (msg.sender.lower(), " ".join(msg.text.lower().split()))
        # prune
        for k, t in list(self._seen.items()):
            if now - t > self.window_s:
                del self._seen[k]
        if key in self._seen:
            return True
        self._seen[key] = now
        return False


class AddressDetector:
    def __init__(
        self,
        agent_name: str,
        aliases: list[str] | None = None,
        owner: str | None = None,
        followup_window_s: float = 45.0,
        nearby_radius: float = 32.0,
    ):
        self.agent_name = agent_name
        self.owner = (owner or "").strip() or None
        self.followup_window_s = followup_window_s
        self.nearby_radius = nearby_radius
        names = [agent_name, *(aliases or [])]
        alt = "|".join(re.escape(n).replace(r"\ ", r"[\s_]*") for n in names if n)
        self._name_re = re.compile(rf"(?<![A-Za-z0-9_])(?:@)?(?:{alt})(?![A-Za-z0-9_])", re.I)
        self._last_reply_to: dict[str, float] = {}

    def note_reply(self, player: str, now: float | None = None) -> None:
        """Call when the agent replies to a player (opens a follow-up window)."""
        self._last_reply_to[player.lower()] = now if now is not None else time.time()

    def is_from_self(self, msg: ChatMessage) -> bool:
        return msg.sender.lower() == self.agent_name.lower()

    def is_authorised(self, msg: ChatMessage) -> bool:
        return self.owner is None or msg.sender.lower() == self.owner.lower()

    def mentions_agent(self, text: str) -> bool:
        return bool(self._name_re.search(text))

    def decide(
        self,
        msg: ChatMessage,
        nearby_players: Callable[[], list[dict]] | None = None,
        now: float | None = None,
    ) -> AddressDecision:
        now = now if now is not None else time.time()
        if self.is_from_self(msg):
            return AddressDecision(False, "own message")
        if not self.is_authorised(msg):
            return AddressDecision(False, f"sender is not the owner ({self.owner})")
        if msg.private:
            return AddressDecision(True, "direct message")
        if self.mentions_agent(msg.text):
            return AddressDecision(True, "name mentioned")
        last = self._last_reply_to.get(msg.sender.lower())
        if last is not None and now - last <= self.followup_window_s:
            return AddressDecision(True, "follow-up to recent conversation")
        if _YOU.search(msg.text) and nearby_players is not None:
            others = [
                p for p in nearby_players()
                if p["name"].lower() not in (msg.sender.lower(), self.agent_name.lower())
                and p.get("distance", 0) <= self.nearby_radius
            ]
            if not others:
                return AddressDecision(True, "'you' with no other players nearby")
        return AddressDecision(False, "not addressed")
