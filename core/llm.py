"""OpenAI-compatible LLM client (LM Studio by default)."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from openai import AsyncOpenAI, APIConnectionError, APITimeoutError, BadRequestError, InternalServerError

log = logging.getLogger(__name__)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    raw_arguments: str
    parse_error: str | None = None


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    reasoning: str | None = None
    # The assistant message in OpenAI wire format, to append to history verbatim.
    message: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] | None = None


class LLMClient:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.model = cfg["model"]
        self.client = AsyncOpenAI(
            base_url=cfg["base_url"],
            api_key=cfg.get("api_key") or "lm-studio",
            timeout=cfg.get("request_timeout_s", 300),
            max_retries=0,
        )

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        tool_choice: str | None = None,
        reasoning_effort: str | None | bool = True,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "max_tokens": max_tokens or self.cfg.get("max_tokens", 2048),
            "temperature": self.cfg.get("temperature", 0.4) if temperature is None else temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice or "auto"
        effort = self.cfg.get("reasoning_effort") if reasoning_effort is True else reasoning_effort
        if effort:
            kwargs["extra_body"] = {"reasoning_effort": effort}

        try:
            resp = await self._with_retries(kwargs)
        except Exception:
            _dump_failed_request(kwargs)
            raise
        choice = resp.choices[0]
        msg = choice.message
        content = (msg.content or "").strip()
        reasoning = getattr(msg, "reasoning", None) or getattr(msg, "reasoning_content", None)

        calls: list[ToolCall] = []
        wire_calls = []
        for tc in msg.tool_calls or []:
            raw = tc.function.arguments or "{}"
            args, err = _parse_args(raw)
            calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args, raw_arguments=raw, parse_error=err))
            wire_calls.append({
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": raw},
            })

        message: dict[str, Any] = {"role": "assistant", "content": content}
        if wire_calls:
            message["tool_calls"] = wire_calls
        usage = resp.usage.model_dump() if resp.usage else None
        return LLMResponse(content=content, tool_calls=calls, reasoning=reasoning, message=message, usage=usage)

    async def _with_retries(self, kwargs: dict[str, Any], attempts: int = 6):
        """LM Studio's gpt-oss output parser rejects a fraction of samples at random
        ("does not match the expected ... format"). Resample, nudging the
        temperature up so retries don't reproduce the same output."""
        base_temp = kwargs.get("temperature", 0.4)
        for i in range(attempts):
            try:
                return await self.client.chat.completions.create(**kwargs)
            except (APIConnectionError, APITimeoutError, InternalServerError, BadRequestError) as exc:
                if isinstance(exc, BadRequestError) and not _is_transient_400(exc):
                    raise
                if i == attempts - 1:
                    raise
                transient_format = isinstance(exc, BadRequestError)
                delay = 0.3 if transient_format else min(2.0 * (2 ** i), 20)
                log.debug("LLM request failed (%s); retry %d in %.1fs", exc.__class__.__name__, i + 1, delay)
                if transient_format:
                    kwargs["temperature"] = min(1.0, base_temp + 0.15 * (i + 1))
                await asyncio.sleep(delay)
        raise RuntimeError("unreachable")


def _dump_failed_request(kwargs: dict[str, Any]) -> None:
    try:
        from core.config import ROOT
        path = ROOT / "logs" / "last_failed_llm_request.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(kwargs, indent=1, default=str))
    except Exception:  # noqa: BLE001
        pass


def _is_transient_400(exc: BadRequestError) -> bool:
    text = str(exc).lower()
    return any(k in text for k in ("does not match the expected", "engine protocol", "server_error", "failed to parse"))


def _parse_args(raw: str) -> tuple[dict[str, Any], str | None]:
    raw = (raw or "").strip() or "{}"
    try:
        val = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {}, f"could not parse tool arguments as JSON: {exc}"
    if not isinstance(val, dict):
        return {}, "tool arguments must be a JSON object"
    return val, None
