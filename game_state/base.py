"""Game adapter interface.

Each supported game provides an adapter that knows how to connect to the game,
read its state, and stream events. Action execution goes through the
ActionBackend for that game (see actions/), which the adapter constructs.
Adding a new game = new adapter + new backend + config/games/<game>.yaml.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator


@dataclass
class GameEvent:
    type: str  # chat | whisper | death | hurt | spawn | kicked | disconnected | player_joined | ...
    data: dict[str, Any] = field(default_factory=dict)
    time: float = 0.0


class GameAdapter(ABC):
    game_id: str = "unknown"
    display_name: str = "Unknown game"
    #: The agent's actual in-game name once connected (may differ from config,
    #: e.g. a Microsoft account's Minecraft profile name).
    username: str | None = None

    @abstractmethod
    async def start(self) -> None:
        """Connect to the game (launch helpers, wait until ready)."""

    @abstractmethod
    async def stop(self) -> None:
        """Disconnect and clean up."""

    @abstractmethod
    async def get_state(self) -> dict[str, Any]:
        """Structured snapshot of the game state."""

    @abstractmethod
    def format_state(self, state: dict[str, Any]) -> str:
        """Render state as compact text for the LLM."""

    @abstractmethod
    def format_status_line(self, state: dict[str, Any]) -> str:
        """One-line status summary appended to tool results."""

    @abstractmethod
    def events(self) -> AsyncIterator[GameEvent]:
        """Stream game events (chat, damage, death, ...)."""

    @abstractmethod
    def action_backend(self):
        """The ActionBackend that executes tools for this game."""

    def nearby_players(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        """Players near the agent: [{name, distance}]. Used for addressee inference."""
        return []

    def is_following(self, state: dict[str, Any]) -> bool:
        return False
