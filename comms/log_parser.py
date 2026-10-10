"""Parse in-game chat out of game server console logs.

Each game gets a parser function: line -> ChatMessage | None.
"""

from __future__ import annotations

import re
from typing import Callable

from comms.messages import ChatMessage

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# Vanilla: "[01:28:34] [Server thread/INFO]: <Steve> hello"
# Paper:   "[01:28:34 INFO]: <Steve> hello"
# Signed-chat edge case: "[Not Secure] <Steve> hello"
_MC_CHAT = re.compile(
    r"^\[[^\]]*\](?:\s*\[[^\]]*\])?:\s*(?:\[Not Secure\]\s*)?<(?P<sender>[A-Za-z0-9_]{1,16})>\s(?P<text>.*)$"
)
# Vanilla /say from a player: "[...]: [Steve] text" (but not "[Server]" / "[Rcon]")
_MC_SAY = re.compile(r"^\[[^\]]*\](?:\s*\[[^\]]*\])?:\s*\[(?P<sender>[A-Za-z0-9_]{1,16})\]\s(?P<text>.*)$")
_SYSTEM_SENDERS = {"Server", "Rcon", "server", "rcon"}


def parse_minecraft_line(line: str) -> ChatMessage | None:
    line = _ANSI.sub("", line).strip()
    if not line:
        return None
    m = _MC_CHAT.match(line)
    if not m:
        m = _MC_SAY.match(line)
        if not m or m.group("sender") in _SYSTEM_SENDERS:
            return None
    text = m.group("text").strip()
    if not text:
        return None
    return ChatMessage(sender=m.group("sender"), text=text, source="docker")


PARSERS: dict[str, Callable[[str], ChatMessage | None]] = {
    "minecraft": parse_minecraft_line,
}
