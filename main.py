"""Player Two — entry point.

    python main.py                 # run the agent with config/settings.yaml
    python main.py --check         # connect, print the game state, exit
    python main.py --no-autonomy   # only act on player instructions
    python main.py --set game.server.port=25566 --set game.server.auth=offline
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys

import yaml

from core.agent import Agent
from core.config import load_config, resolve_path
from core.llm import LLMClient
from core.server_api import ServerAPI
from game_state.registry import create_adapter


def parse_overrides(pairs: list[str]) -> dict:
    """--set a.b.c=value  (prefix 'game.' targets the game config)."""
    out: dict = {}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"--set expects key=value, got {pair!r}")
        key, raw = pair.split("=", 1)
        value = yaml.safe_load(raw)
        parts = key.split(".")
        if parts[0] == "game":
            parts = ["game_config", *parts[1:]]
        node = out
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = value
    return out


def setup_logging(cfg) -> None:
    level = getattr(logging, str(cfg.get_path("logging.level", "INFO")).upper(), logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")
    root = logging.getLogger()
    root.setLevel(level)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)
    log_file = cfg.get_path("logging.file")
    if log_file:
        path = resolve_path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(path)
        fh.setFormatter(fmt)
        root.addHandler(fh)
    for noisy in ("httpx", "httpx2", "httpcore", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


async def amain(args: argparse.Namespace) -> int:
    overrides = parse_overrides(args.set or [])
    if args.no_autonomy:
        overrides.setdefault("autonomy", {})["enabled"] = False
    cfg = load_config(overrides=overrides)
    setup_logging(cfg)
    log = logging.getLogger("player_two")

    game_cfg = cfg.game_config
    api_base = cfg.get_path("server_api.base_url")
    if api_base and cfg.get_path("server_api.auto_start"):
        try:
            await ServerAPI(api_base).start(game_cfg["game_id"])
        except Exception as exc:  # noqa: BLE001
            log.warning("server API start failed: %s", exc)

    adapter = create_adapter(game_cfg["game_id"], game_cfg, cfg.agent.name)
    llm = LLMClient(cfg.llm)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    try:
        await adapter.start()
        if args.check:
            state = await adapter.get_state()
            print(adapter.format_state(state))
            return 0
        agent = Agent(cfg, llm, adapter)
        runner = asyncio.create_task(agent.run())
        stopper = asyncio.create_task(stop.wait())
        done, _ = await asyncio.wait({runner, stopper}, return_when=asyncio.FIRST_COMPLETED)
        if runner in done:
            runner.result()  # surface crashes
        runner.cancel()
        log.info("shutting down")
        return 0
    finally:
        await adapter.stop()


def main() -> None:
    ap = argparse.ArgumentParser(description="Player Two — AI game companion")
    ap.add_argument("--check", action="store_true", help="connect, print game state, exit")
    ap.add_argument("--no-autonomy", action="store_true", help="only act on player instructions")
    ap.add_argument("--set", action="append", metavar="KEY=VALUE", help="override a config value")
    args = ap.parse_args()
    sys.exit(asyncio.run(amain(args)))


if __name__ == "__main__":
    main()
