import json

from core.agent import trim_history
from core.llm import _parse_args
from core.planner import parse_goal
from game_state.minecraft.formatter import format_state, format_status_line

STATE = {
    "self": {"username": "PlayerTwo", "position": {"x": 1, "y": 64, "z": 2}, "dimension": "overworld",
             "health": 18.5, "food": 17, "gamemode": "survival",
             "equipment": {"hand": "stone_pickaxe", "head": None, "torso": "iron_chestplate", "legs": None, "feet": None}},
    "world": {"time_of_day": 14000, "is_day": False, "raining": True, "standing_on": "grass_block", "sky_visible": True},
    "inventory": {"items": {"oak_log": 3, "cobblestone": 20}, "free_slots": 30},
    "nearby": {"players": [{"name": "CinnamonLatte", "id": 5, "position": {"x": 3, "y": 64, "z": 2}, "distance": 2.0}],
               "hostile_mobs": [{"type": "zombie", "id": 9, "position": {"x": 9, "y": 64, "z": 2}, "distance": 8.0}],
               "animals": [], "dropped_items": [],
               "blocks": {"iron_ore": {"count": 2, "nearest": {"x": 0, "y": 50, "z": 0}, "distance": 14.1}}},
    "online_players": ["CinnamonLatte"],
    "activity": {"current_action": None, "following": "CinnamonLatte"},
}


def test_format_state_mentions_key_facts():
    text = format_state(STATE)
    for needle in ["(1, 64, 2)", "health 18.5/20", "night", "raining", "stone_pickaxe", "iron_chestplate",
                   "cobblestone x20", "CinnamonLatte", "zombie id=9", "iron_ore x2", "Currently following"]:
        assert needle in text, needle


def test_status_line():
    line = format_status_line(STATE)
    assert "health 18.5/20" in line and "zombie" in line


def test_parse_args():
    assert _parse_args('{"x": 1}') == ({"x": 1}, None)
    assert _parse_args("") == ({}, None)
    args, err = _parse_args("{oops")
    assert args == {} and err


def test_parse_goal():
    assert parse_goal('{"goal": "Mine iron", "reason": "tools"}') == "Mine iron"
    assert parse_goal('Sure! {"goal": "Get food"}') == "Get food"
    assert parse_goal("Chop some trees") == "Chop some trees"
    assert parse_goal("") is None


def test_trim_history_never_starts_with_tool():
    msgs = [{"role": "system"}, {"role": "user"}]
    for i in range(10):
        msgs.append({"role": "assistant", "tool_calls": [{"id": str(i)}]})
        msgs.append({"role": "tool", "tool_call_id": str(i)})
    out = trim_history(msgs, 5)
    assert out[:2] == msgs[:2]
    assert out[2]["role"] == "assistant"
    assert len(out) <= 7
    assert trim_history(msgs[:4], 5) == msgs[:4]
