"""Maps game ids to adapters. Register new games here."""

from __future__ import annotations

from game_state.base import GameAdapter


def create_adapter(game_id: str, game_cfg: dict, agent_name: str) -> GameAdapter:
    if game_id == "minecraft":
        from game_state.minecraft.adapter import MinecraftAdapter
        return MinecraftAdapter(game_cfg, agent_name)
    raise ValueError(f"no adapter for game '{game_id}' yet")
