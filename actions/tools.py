"""Universal tool definitions.

These are the game-agnostic action primitives the LLM can choose from. Each
game's ActionBackend declares which of them it supports; only those are offered
to the model. Descriptions are written for the model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    #: Core tools are handled by the agent itself rather than the game backend.
    core: bool = False

    def to_openai(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }


def _obj(props: dict[str, Any] | None = None, required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": props or {}, "required": required or []}


_INT = {"type": "integer"}
_NUM = {"type": "number"}
_STR = {"type": "string"}

XYZ = {"x": _NUM, "y": _NUM, "z": _NUM}

TOOLS: list[ToolSpec] = [
    # --- core (agent-level) ---
    ToolSpec(
        "respond_to_player",
        "Send a short chat message to the player (one or two casual sentences, no markdown). "
        "Use it to acknowledge requests, answer questions, and report results.",
        _obj({"message": _STR}, ["message"]),
        core=True,
    ),
    ToolSpec(
        "get_game_state",
        "Get a fresh, full description of your surroundings, inventory and status.",
        _obj(),
        core=True,
    ),
    ToolSpec(
        "task_complete",
        "Call when the current task is finished, impossible, or needs nothing more from you. "
        "Summarise what happened in a few words.",
        _obj({"summary": _STR, "success": {"type": "boolean"}}, ["summary"]),
        core=True,
    ),
    # --- movement ---
    ToolSpec(
        "move_to_position",
        "Walk/path to coordinates. range = how close is close enough (default 1).",
        _obj({**XYZ, "range": _NUM}, ["x", "y", "z"]),
    ),
    ToolSpec(
        "go_to_player",
        "Walk to a player once (e.g. 'come here').",
        _obj({"player_name": _STR, "distance": _NUM}, ["player_name"]),
    ),
    ToolSpec(
        "follow_player",
        "Keep following a player continuously until told to stop or given another movement task. "
        "Returns immediately; the following continues in the background.",
        _obj({"player_name": _STR, "distance": _NUM}, ["player_name"]),
    ),
    ToolSpec("stop", "Stop moving, following, mining or attacking.", _obj()),
    ToolSpec(
        "go_to_surface",
        "Dig straight up and pillar until you reach open sky. Use when stuck underground or when paths keep failing.",
        _obj(),
    ),
    # --- gathering / world interaction ---
    ToolSpec(
        "collect_block",
        "Find the nearest blocks of a type, walk to them, mine them and pick up the drops. "
        "Accepts loose names like 'log', 'oak_log', 'stone', 'iron_ore', 'coal'.",
        _obj({"block_type": _STR, "count": _INT, "max_distance": _INT}, ["block_type"]),
    ),
    ToolSpec(
        "mine_block",
        "Mine the single block at exact coordinates and pick up its drop.",
        _obj(XYZ, ["x", "y", "z"]),
    ),
    ToolSpec(
        "find_blocks",
        "Locate the nearest blocks of a type without mining them. Returns positions.",
        _obj({"block_type": _STR, "max_distance": _INT, "count": _INT}, ["block_type"]),
    ),
    ToolSpec(
        "place_block",
        "Place a block from your inventory at coordinates, or next to you if coordinates are omitted.",
        _obj({"item_name": _STR, **XYZ}, ["item_name"]),
    ),
    # --- crafting / items ---
    ToolSpec(
        "craft_item",
        "Craft an item. quantity is the number of items wanted. Uses/places a crafting table when needed. "
        "Tells you which ingredients are missing if it can't.",
        _obj({"item_name": _STR, "quantity": _INT}, ["item_name"]),
    ),
    ToolSpec(
        "smelt_item",
        "Smelt items in a nearby furnace (placing one from inventory if needed), e.g. raw_iron -> iron_ingot. "
        "Uses fuel from your inventory.",
        _obj({"item_name": _STR, "quantity": _INT}, ["item_name"]),
    ),
    ToolSpec(
        "equip_item",
        "Equip an item. destination: hand, off-hand, head, torso, legs, feet (armor is detected automatically).",
        _obj({"item_name": _STR, "destination": _STR}, ["item_name"]),
    ),
    ToolSpec("eat", "Eat food (best available if item_name omitted).", _obj({"item_name": _STR})),
    ToolSpec(
        "give_item",
        "Walk to a player and hand them items.",
        _obj({"player_name": _STR, "item_name": _STR, "count": _INT}, ["player_name", "item_name"]),
    ),
    ToolSpec(
        "drop_item",
        "Drop items from your inventory on the ground (all of that item if count omitted).",
        _obj({"item_name": _STR, "count": _INT}, ["item_name"]),
    ),
    # --- combat / misc ---
    ToolSpec(
        "attack_entity",
        "Fight a mob until it dies. Give entity_id (from game state) or entity_type (e.g. 'zombie', 'cow'). "
        "Never attacks players.",
        _obj({"entity_id": _INT, "entity_type": _STR}),
    ),
    ToolSpec("sleep_in_bed", "Sleep in a nearby bed (only works at night).", _obj()),
]

TOOLS_BY_NAME: dict[str, ToolSpec] = {t.name: t for t in TOOLS}
CORE_TOOLS = frozenset(t.name for t in TOOLS if t.core)
