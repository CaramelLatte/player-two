"""Optional persona voice: re-voice chat lines with a roleplay model.

This is also the seam where Voxta personas will plug in later.
"""

from __future__ import annotations

import logging

from core.llm import LLMClient

log = logging.getLogger(__name__)


class PersonaVoice:
    def __init__(self, llm: LLMClient, persona: str, cfg: dict):
        self.llm = llm
        self.persona = persona
        self.enabled = bool(cfg.get("enabled"))
        self.model = cfg.get("model")
        self.max_tokens = cfg.get("max_tokens", 120)

    async def voice(self, text: str) -> str:
        if not self.enabled or not self.model:
            return text
        messages = [
            {"role": "system", "content": f"{self.persona}\n\nRewrite the given in-game chat line in your own voice. "
                                          "Keep the meaning and any numbers/coordinates exactly. One or two short "
                                          "sentences, no quotes, no emoji spam. Output only the line."},
            {"role": "user", "content": text},
        ]
        try:
            resp = await self.llm.chat(messages, model=self.model, max_tokens=self.max_tokens,
                                       temperature=0.8, reasoning_effort=None)
            out = resp.content.strip().strip('"')
            return out or text
        except Exception as exc:  # noqa: BLE001
            log.warning("persona voice failed, using plain text: %s", exc)
            return text
