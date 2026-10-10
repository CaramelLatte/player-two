"""Chat sources. Each yields ChatMessage objects into a shared queue."""

from __future__ import annotations

import asyncio
import logging
import shutil
from typing import Callable

from comms.messages import ChatMessage

log = logging.getLogger(__name__)


class DockerLogSource:
    """Follows `docker logs -f <container>` and parses chat lines."""

    name = "docker"

    def __init__(self, container: str, parser: Callable[[str], ChatMessage | None]):
        self.container = container
        self.parser = parser

    async def run(self, out: asyncio.Queue[ChatMessage]) -> None:
        if not shutil.which("docker"):
            log.warning("docker not found; container log chat source disabled")
            return
        warned = False
        while True:
            proc = await asyncio.create_subprocess_exec(
                "docker", "logs", "-f", "--tail", "0", self.container,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
            assert proc.stdout
            got_line = False
            try:
                async for raw in proc.stdout:
                    got_line = True
                    line = raw.decode(errors="replace").rstrip("\n")
                    if "No such container" in line or "Error response from daemon" in line:
                        if not warned:
                            log.warning("docker log source: %s", line.strip())
                            warned = True
                        break
                    msg = self.parser(line)
                    if msg:
                        await out.put(msg)
            finally:
                if proc.returncode is None:
                    proc.kill()
                await proc.wait()
            if got_line:
                warned = False
            await asyncio.sleep(5)  # container restarted / missing: retry


class BridgeChatSource:
    """Chat and whispers received by the in-game client itself.

    Fed by the agent's game-event loop (the adapter owns the event stream), so
    this just exposes a push() for those events.
    """

    name = "bridge"

    def __init__(self) -> None:
        self._out: asyncio.Queue[ChatMessage] | None = None

    async def run(self, out: asyncio.Queue[ChatMessage]) -> None:
        self._out = out

    def push(self, sender: str, text: str, private: bool = False) -> None:
        if self._out is not None:
            self._out.put_nowait(ChatMessage(sender=sender, text=text, source="bridge", private=private))
