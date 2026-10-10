"""Thin wrapper around a virtual keyboard+mouse via python-evdev.

Not used by the Minecraft build. Requires `pip install evdev` and write access
to /dev/uinput (add your user to the `input` group or a udev rule).
"""

from __future__ import annotations

import asyncio


class VirtualInput:
    def __init__(self, name: str = "player-two-virtual-input"):
        try:
            from evdev import UInput, ecodes  # type: ignore
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("python-evdev is required for the uinput backend: pip install evdev") from exc
        self._ecodes = ecodes
        keys = [v for k, v in ecodes.ecodes.items() if k.startswith("KEY_")]
        caps = {
            ecodes.EV_KEY: keys + [ecodes.BTN_LEFT, ecodes.BTN_RIGHT, ecodes.BTN_MIDDLE],
            ecodes.EV_REL: [ecodes.REL_X, ecodes.REL_Y, ecodes.REL_WHEEL],
        }
        self._ui = UInput(caps, name=name)

    def _key(self, code: int, value: int) -> None:
        self._ui.write(self._ecodes.EV_KEY, code, value)
        self._ui.syn()

    def key_code(self, name: str) -> int:
        return getattr(self._ecodes, name if name.startswith(("KEY_", "BTN_")) else f"KEY_{name.upper()}")

    async def tap(self, key: str, hold_s: float = 0.05) -> None:
        code = self.key_code(key)
        self._key(code, 1)
        await asyncio.sleep(hold_s)
        self._key(code, 0)

    async def hold(self, key: str, seconds: float) -> None:
        await self.tap(key, hold_s=seconds)

    def move_mouse(self, dx: int, dy: int) -> None:
        self._ui.write(self._ecodes.EV_REL, self._ecodes.REL_X, dx)
        self._ui.write(self._ecodes.EV_REL, self._ecodes.REL_Y, dy)
        self._ui.syn()

    async def click(self, button: str = "left", hold_s: float = 0.05) -> None:
        await self.tap({"left": "BTN_LEFT", "right": "BTN_RIGHT", "middle": "BTN_MIDDLE"}[button], hold_s)

    def close(self) -> None:
        self._ui.close()
