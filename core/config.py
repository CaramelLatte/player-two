"""Configuration loading: config/settings.yaml + config/games/<game>.yaml."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


class Cfg(dict):
    """Dict with attribute access for nested config sections."""

    def __getattr__(self, key: str) -> Any:
        try:
            value = self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc
        return Cfg(value) if isinstance(value, dict) and not isinstance(value, Cfg) else value

    def get_path(self, dotted: str, default: Any = None) -> Any:
        node: Any = self
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(settings_path: Path | None = None, overrides: dict | None = None) -> Cfg:
    settings_path = settings_path or CONFIG_DIR / "settings.yaml"
    settings = yaml.safe_load(settings_path.read_text()) or {}
    if overrides:
        settings = _deep_merge(settings, overrides)

    game = settings.get("game", "minecraft")
    game_path = CONFIG_DIR / "games" / f"{game}.yaml"
    if not game_path.exists():
        raise FileNotFoundError(f"no game config at {game_path}")
    game_cfg = yaml.safe_load(game_path.read_text()) or {}
    if overrides and "game_config" in overrides:
        game_cfg = _deep_merge(game_cfg, overrides["game_config"])
    settings["game_config"] = game_cfg

    persona_file = settings.get("agent", {}).get("persona_file")
    persona = ""
    if persona_file:
        p = (ROOT / persona_file) if not Path(persona_file).is_absolute() else Path(persona_file)
        if p.exists():
            persona = p.read_text().strip()
    settings.setdefault("agent", {})["persona"] = persona
    return Cfg(settings)


def resolve_path(p: str) -> Path:
    path = Path(p)
    return path if path.is_absolute() else ROOT / path
