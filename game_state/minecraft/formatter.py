"""Render Mineflayer bridge state as compact text for the LLM."""

from __future__ import annotations

from typing import Any


def _pos(p: dict[str, Any] | None) -> str:
    if not p:
        return "?"
    return f"({p['x']}, {p['y']}, {p['z']})"


def _time_label(tod: int) -> str:
    if tod < 1000 or tod >= 23000:
        return "dawn"
    if tod < 6000:
        return "morning"
    if tod < 11000:
        return "afternoon"
    if tod < 13000:
        return "dusk"
    return "night"


def format_state(state: dict[str, Any]) -> str:
    s = state["self"]
    w = state["world"]
    inv = state["inventory"]
    near = state["nearby"]
    lines: list[str] = []

    lines.append(
        f"You ({s['username']}) at {_pos(s['position'])} in the {s['dimension']}, "
        f"health {s['health']}/20, food {s['food']}/20, gamemode {s['gamemode']}."
    )
    world_bits = [f"{_time_label(w['time_of_day'])} (time {w['time_of_day']})"]
    if w.get("raining"):
        world_bits.append("raining")
    if w.get("standing_on"):
        world_bits.append(f"standing on {w['standing_on']}")
    if w.get("sky_visible") is False:
        world_bits.append("under cover/underground")
    if w.get("biome"):
        world_bits.append(f"biome {w['biome']}")
    lines.append("World: " + ", ".join(world_bits) + ".")

    eq = s.get("equipment", {})
    armor = [v for k, v in eq.items() if k in ("head", "torso", "legs", "feet") and v]
    lines.append(f"Holding: {eq.get('hand') or 'nothing'}. Armor: {', '.join(armor) or 'none'}.")

    items = inv.get("items", {})
    if items:
        inv_txt = ", ".join(f"{k} x{v}" for k, v in sorted(items.items(), key=lambda kv: -kv[1]))
    else:
        inv_txt = "empty"
    lines.append(f"Inventory ({inv.get('free_slots', '?')} free slots): {inv_txt}.")

    if near.get("players"):
        lines.append("Players nearby: " + "; ".join(
            f"{p['name']} at {_pos(p['position'])} {p['distance']}m" for p in near["players"]))
    else:
        lines.append("Players nearby: none in sight.")
    if near.get("hostile_mobs"):
        lines.append("Hostile mobs: " + "; ".join(
            f"{m['type']} id={m['id']} {m['distance']}m" for m in near["hostile_mobs"]))
    if near.get("animals"):
        lines.append("Animals: " + "; ".join(
            f"{a['type']} id={a['id']} {a['distance']}m" for a in near["animals"][:6]))
    if near.get("dropped_items"):
        lines.append("Dropped items: " + "; ".join(
            f"{d['item']} at {_pos(d['position'])}" for d in near["dropped_items"][:6]))
    blocks = near.get("blocks") or {}
    if blocks:
        ordered = sorted(blocks.items(), key=lambda kv: kv[1]["distance"])[:14]
        lines.append("Notable blocks within 16m: " + "; ".join(
            f"{name} x{b['count']} (nearest {_pos(b['nearest'])} {b['distance']}m)" for name, b in ordered))

    others = state.get("online_players") or []
    lines.append(f"Online players: {', '.join(others) if others else 'none besides you'}.")
    act = state.get("activity") or {}
    if act.get("following"):
        lines.append(f"Currently following: {act['following']}.")
    elif act.get("current_action"):
        lines.append(f"Currently doing: {act['current_action']}.")
    return "\n".join(lines)


def format_status_line(state: dict[str, Any]) -> str:
    s = state["self"]
    hostile = state["nearby"].get("hostile_mobs") or []
    bits = [
        f"pos {_pos(s['position'])}",
        f"health {s['health']}/20",
        f"food {s['food']}/20",
        f"held {s.get('equipment', {}).get('hand') or 'nothing'}",
    ]
    if hostile:
        bits.append("hostiles: " + ", ".join(f"{m['type']} {m['distance']}m" for m in hostile[:3]))
    return "; ".join(bits)
