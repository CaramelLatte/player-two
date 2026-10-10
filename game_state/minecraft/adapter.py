"""Minecraft game adapter: launches/supervises the Mineflayer bridge."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any, AsyncIterator

import httpx

from actions.minecraft import MineflayerBackend
from core.config import resolve_path
from game_state.base import GameAdapter, GameEvent
from game_state.minecraft import formatter
from game_state.minecraft.bridge_client import BridgeClient, BridgeError

log = logging.getLogger(__name__)

BRIDGE_DIR = Path(__file__).resolve().parent / "bridge"


class MinecraftAdapter(GameAdapter):
    game_id = "minecraft"
    display_name = "Minecraft (Java Edition)"

    def __init__(self, game_cfg: dict[str, Any], agent_name: str):
        self.cfg = game_cfg
        self.agent_name = agent_name
        b = game_cfg.get("bridge", {})
        self.bridge_url = f"http://{b.get('host', '127.0.0.1')}:{b.get('port', 3001)}"
        self.client = BridgeClient(self.bridge_url, action_timeout_s=b.get("action_timeout_s", 120))
        self.backend = MineflayerBackend(self.client)
        self._proc: asyncio.subprocess.Process | None = None
        self._log_fh = None

    # -- lifecycle ---------------------------------------------------------

    async def start(self, connect_timeout_s: float = 300) -> None:
        bridge_cfg = self.cfg.get("bridge", {})
        if await self._bridge_reachable():
            log.info("using already-running bridge at %s", self.bridge_url)
        elif bridge_cfg.get("manage", True):
            await self._launch_bridge()
        else:
            raise RuntimeError(f"bridge not reachable at {self.bridge_url} and bridge.manage is false")

        # Wait for the bot to join the server. Long timeout: first-time Microsoft
        # login needs the user to complete the device-code flow.
        deadline = time.monotonic() + connect_timeout_s
        last_note = 0.0
        while time.monotonic() < deadline:
            if self._proc and self._proc.returncode is not None:
                raise RuntimeError(f"bridge exited with code {self._proc.returncode}; see {bridge_cfg.get('log_file')}")
            try:
                h = await self.client.health()
                if h.get("connected"):
                    self.username = h.get("username") or self.agent_name
                    log.info("bot %s connected (Minecraft %s)", h.get("username"), h.get("version"))
                    return
            except httpx.HTTPError:
                pass
            if time.monotonic() - last_note > 15:
                log.info("waiting for bot to join %s:%s ... (check %s if this takes long)",
                         self.cfg["server"]["host"], self.cfg["server"]["port"], bridge_cfg.get("log_file"))
                last_note = time.monotonic()
            await asyncio.sleep(1)
        raise TimeoutError("bot did not connect to the Minecraft server in time")

    async def _bridge_reachable(self) -> bool:
        try:
            await self.client.health()
            return True
        except httpx.HTTPError:
            return False

    async def _launch_bridge(self) -> None:
        node = shutil.which("node")
        if not node:
            raise RuntimeError("node is not installed / not on PATH")
        if not (BRIDGE_DIR / "node_modules").exists():
            raise RuntimeError(f"bridge dependencies missing: run `npm install` in {BRIDGE_DIR}")

        server = self.cfg["server"]
        bridge = self.cfg.get("bridge", {})
        env = dict(os.environ)
        env.update({
            "MC_HOST": str(server.get("host", "localhost")),
            "MC_PORT": str(server.get("port", 25565)),
            "MC_USERNAME": self.agent_name,
            "MC_AUTH": str(server.get("auth", "offline")),
            "BRIDGE_HOST": str(bridge.get("host", "127.0.0.1")),
            "BRIDGE_PORT": str(bridge.get("port", 3001)),
            "ACTION_TIMEOUT_MS": str(int(bridge.get("action_timeout_s", 120) * 1000)),
            "AUTO_EAT": "true" if bridge.get("auto_eat", True) else "false",
        })
        if server.get("version"):
            env["MC_VERSION"] = str(server["version"])

        log_path = resolve_path(bridge.get("log_file", "logs/bridge.log"))
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_fh = open(log_path, "a", buffering=1)
        log.info("launching bridge (log: %s)", log_path)
        self._proc = await asyncio.create_subprocess_exec(
            node, "index.js", cwd=str(BRIDGE_DIR), env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        asyncio.create_task(self._pump_bridge_output())

    async def _pump_bridge_output(self) -> None:
        assert self._proc and self._proc.stdout
        async for raw in self._proc.stdout:
            line = raw.decode(errors="replace").rstrip()
            if self._log_fh:
                self._log_fh.write(line + "\n")
            # Surface the Microsoft login prompt on the main console.
            if "MICROSOFT LOGIN" in line or "enter code" in line:
                log.warning("%s", line.split("[bridge]", 1)[-1].strip())

    async def stop(self) -> None:
        try:
            await self.backend.cancel()
        except Exception:  # noqa: BLE001
            pass
        await self.client.close()
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), 5)
            except asyncio.TimeoutError:
                self._proc.kill()
        if self._log_fh:
            self._log_fh.close()

    # -- state -------------------------------------------------------------

    async def get_state(self) -> dict[str, Any]:
        return await self.client.state()

    def format_state(self, state: dict[str, Any]) -> str:
        return formatter.format_state(state)

    def format_status_line(self, state: dict[str, Any]) -> str:
        return formatter.format_status_line(state)

    def nearby_players(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"name": p["name"], "distance": p["distance"]} for p in state["nearby"].get("players", [])]

    def is_following(self, state: dict[str, Any]) -> bool:
        return bool((state.get("activity") or {}).get("following"))

    def action_backend(self) -> MineflayerBackend:
        return self.backend

    # -- events ------------------------------------------------------------

    async def events(self) -> AsyncIterator[GameEvent]:
        # Start from "now": don't replay chat from before we started.
        last_id = 0
        try:
            _, last_id = await self.client.events(0, wait_s=0)
        except httpx.HTTPError:
            pass
        while True:
            try:
                evs, new_last = await self.client.events(last_id, wait_s=25)
            except (httpx.HTTPError, BridgeError) as exc:
                log.debug("event poll failed: %s", exc)
                await asyncio.sleep(2)
                continue
            if new_last < last_id:  # bridge restarted
                last_id = 0
                continue
            for ev in evs:
                last_id = max(last_id, ev["id"])
                yield GameEvent(type=ev["type"], data=ev.get("data") or {}, time=ev.get("time", time.time()))
