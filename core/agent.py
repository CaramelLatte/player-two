"""The agent: comms intake, task scheduling and the LLM tool-call loop."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from actions.base import ToolResult
from actions.registry import ToolRegistry
from comms.intent import AddressDetector, Deduplicator
from comms.log_parser import PARSERS
from comms.messages import ChatMessage
from comms.outputs import GameChatOutput, OutputRouter
from comms.sources import BridgeChatSource, DockerLogSource
from core import prompts
from core.llm import LLMClient, ToolCall
from core.memory import ShortTermMemory
from core.persona import PersonaVoice
from core.planner import GoalPlanner
from game_state.base import GameAdapter, GameEvent

log = logging.getLogger("player_two")

PRIORITY = {"autonomous": 1, "reaction": 2, "player": 3}
_STOP_WORDS = re.compile(r"^\W*(stop|halt|freeze|wait|cancel|hold on|stay)\W*$", re.I)


@dataclass
class Task:
    kind: str  # player | reaction | autonomous
    instruction: str
    requester: str | None = None
    created: float = field(default_factory=time.time)
    # Filled in while running:
    progress: list[str] = field(default_factory=list)
    interrupted_context: str = ""
    replied: bool = False

    @property
    def priority(self) -> int:
        return PRIORITY[self.kind]

    def label(self) -> str:
        who = f" from {self.requester}" if self.requester else ""
        return f"{self.kind}{who}: {self.instruction[:80]}"


class Agent:
    def __init__(self, cfg, llm: LLMClient, adapter: GameAdapter):
        self.cfg = cfg
        self.llm = llm
        self.adapter = adapter
        self.backend = adapter.action_backend()
        self.registry = ToolRegistry(self.backend)

        agent = cfg.agent
        # Prefer the real in-game name; the configured name stays a chat alias.
        self.name: str = adapter.username or agent.name
        aliases = list(agent.get("aliases", []))
        if agent.name.lower() != self.name.lower():
            aliases.insert(0, agent.name)
        self.owner: str | None = agent.get("owner") or None
        self.persona: str = agent.get("persona", "")

        comms = cfg.comms
        self.detector = AddressDetector(
            agent_name=self.name,
            aliases=aliases,
            owner=self.owner,
            followup_window_s=comms.get("followup_window_s", 45),
            nearby_radius=comms.get("nearby_radius", 32),
        )
        self.detector_aliases = aliases
        self.dedup = Deduplicator(comms.get("dedup_window_s", 6))
        self.memory = ShortTermMemory()
        self.outputs = OutputRouter([GameChatOutput(self.backend)])
        self.voice = PersonaVoice(llm, self.persona, cfg.llm.get("persona_voice", {}) or {})
        self.planner = GoalPlanner(llm, persona=self.persona, name=self.name,
                                   game=adapter.display_name, owner=self.owner,
                                   model=cfg.llm.get("planner_model"))

        self.inbox: asyncio.Queue[ChatMessage] = asyncio.Queue()
        self.bridge_chat = BridgeChatSource()
        self.sources: list[Any] = []
        enabled = set(comms.get("sources", ["docker", "bridge"]))
        container = cfg.game_config.get("server", {}).get("container")
        parser = PARSERS.get(adapter.game_id)
        if "docker" in enabled and container and parser:
            self.sources.append(DockerLogSource(container, parser))
        self._bridge_chat_enabled = "bridge" in enabled
        if self._bridge_chat_enabled:
            self.sources.append(self.bridge_chat)

        self.current: Task | None = None
        self._current_job: asyncio.Task | None = None
        self.pending: Task | None = None
        self._wake = asyncio.Event()
        self._last_activity = time.time()
        self._last_reaction = 0.0
        self._last_state: dict[str, Any] | None = None
        self._last_state_at = 0.0

        self.autonomy = cfg.autonomy
        self.loop_cfg = cfg.loop
        self.game_notes = cfg.game_config.get("prompt_notes", "")

    # ------------------------------------------------------------------ run

    async def run(self) -> None:
        jobs = [asyncio.create_task(src.run(self.inbox), name=f"source:{src.name}") for src in self.sources]
        jobs += [
            asyncio.create_task(self._game_event_loop(), name="game-events"),
            asyncio.create_task(self._chat_loop(), name="chat"),
            asyncio.create_task(self._scheduler_loop(), name="scheduler"),
            asyncio.create_task(self._state_poll_loop(), name="state-poll"),
        ]
        log.info("Answers to: %s", ", ".join([self.name, *self.detector_aliases]))
        log.info("%s is online. Listening for chat via: %s. Autonomy: %s.", self.name,
                 ", ".join(s.name for s in self.sources) or "nothing",
                 "on" if self.autonomy.get("enabled") else "off")
        try:
            await asyncio.gather(*jobs)
        finally:
            for j in jobs:
                j.cancel()
            if self._current_job:
                self._current_job.cancel()

    # ---------------------------------------------------------- intake

    async def _state_poll_loop(self) -> None:
        """Keep a recent state snapshot around for addressee inference."""
        while True:
            if time.time() - self._last_state_at > 5:
                try:
                    await self._fresh_state()
                except Exception:  # noqa: BLE001
                    pass
            await asyncio.sleep(5)

    async def _game_event_loop(self) -> None:
        async for ev in self.adapter.events():
            await self.handle_game_event(ev)

    async def handle_game_event(self, ev: GameEvent) -> None:
        d = ev.data
        if ev.type in ("chat", "whisper"):
            if self._bridge_chat_enabled:
                self.bridge_chat.push(d.get("sender", ""), d.get("message", ""), private=ev.type == "whisper")
        elif ev.type == "death":
            log.warning("I died")
            self.memory.add("event", "You died and respawned. Your inventory was dropped where you died.")
        elif ev.type == "hurt":
            self.memory.add("event", f"You took damage (health {d.get('health')}).")
            await self._maybe_react(f"You just took damage; health is now {d.get('health')}/20.")
        elif ev.type == "player_joined":
            self.memory.add("event", f"{d.get('player')} joined the game.")
        elif ev.type == "player_left":
            self.memory.add("event", f"{d.get('player')} left the game.")
        elif ev.type == "auto_ate":
            self.memory.add("event", f"You ate {d.get('item')} (food {d.get('food')}/20).")
        elif ev.type in ("kicked", "disconnected"):
            log.warning("bot %s: %s", ev.type, d.get("reason"))
        elif ev.type == "auth_required":
            log.warning("Microsoft login required: open %s and enter code %s", d.get("url"), d.get("code"))

    async def _maybe_react(self, event_text: str) -> None:
        if not self.autonomy.get("react_to_damage", True):
            return
        if self.current and self.current.kind in ("player", "reaction"):
            return
        if time.time() - self._last_reaction < 20:
            return
        self._last_reaction = time.time()
        self.submit(Task(kind="reaction", instruction=event_text))

    async def _chat_loop(self) -> None:
        while True:
            msg = await self.inbox.get()
            await self.handle_chat(msg)

    async def handle_chat(self, msg: ChatMessage) -> None:
        if self.detector.is_from_self(msg) or self.dedup.is_duplicate(msg):
            return
        nearby = self.adapter.nearby_players(self._last_state) if self._last_state else []
        decision = self.detector.decide(msg, nearby_players=lambda: nearby)
        self.memory.chat_in(msg.sender, msg.text, addressed=decision.addressed)
        log.info("chat <%s> %s  [%s: %s]", msg.sender, msg.text,
                 "addressed" if decision.addressed else "ignored", decision.reason)
        if not decision.addressed:
            return
        # Fast path: an explicit "stop" halts the body immediately, before the LLM thinks.
        stripped = self.detector._name_re.sub("", msg.text).strip(" ,!.")
        if _STOP_WORDS.match(stripped or msg.text):
            await self.backend.cancel()
        self.submit(Task(kind="player", instruction=msg.text, requester=msg.sender))

    # ---------------------------------------------------------- scheduling

    def submit(self, task: Task) -> None:
        if self.pending and self.pending.priority > task.priority:
            return
        if self.pending and self.pending.kind == "player" and task.kind == "player":
            # Two quick messages: merge so neither is lost.
            task.instruction = f"{self.pending.instruction}\n<{task.requester}> {task.instruction}"
        self.pending = task
        self._wake.set()

    async def _scheduler_loop(self) -> None:
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

            if self._current_job and self._current_job.done():
                self._current_job = None
                self.current = None

            if self.pending and (self.current is None or self.pending.priority >= self.current.priority):
                task, self.pending = self.pending, None
                if self.current and self._current_job:
                    log.info("interrupting [%s] for [%s]", self.current.label(), task.label())
                    task.interrupted_context = self._interruption_note(self.current)
                    self._current_job.cancel()
                    try:
                        await self._current_job
                    except (asyncio.CancelledError, Exception):  # noqa: BLE001
                        pass
                    await self.backend.cancel()
                self._start(task)
                continue

            if self.current is None and self.autonomy.get("enabled", True):
                idle = time.time() - self._last_activity
                if idle >= self.autonomy.get("idle_before_autonomy_s", 30):
                    await self._start_autonomous()

    def _start(self, task: Task) -> None:
        self.current = task
        self._current_job = asyncio.create_task(self._run_task_safe(task), name=f"task:{task.kind}")
        self._current_job.add_done_callback(lambda _: self._wake.set())

    async def _start_autonomous(self) -> None:
        try:
            state = await self._fresh_state()
        except Exception as exc:  # noqa: BLE001
            log.debug("autonomy: no state (%s)", exc)
            self._last_activity = time.time()
            return
        if self.autonomy.get("pause_while_following", True) and self.adapter.is_following(state):
            return
        if self.pending:  # a player spoke while we were fetching state
            return
        # Mark the slot as taken while the planner thinks, so we don't double-plan.
        placeholder = Task(kind="autonomous", instruction="(choosing a goal)")
        self.current = placeholder

        async def plan_and_run() -> None:
            goal = await self.planner.next_goal(self.adapter.format_state(state), self.memory.render())
            log.info("autonomous goal: %s", goal)
            placeholder.instruction = goal
            await self._run_task_safe(placeholder)

        self._current_job = asyncio.create_task(plan_and_run(), name="task:autonomous")
        self._current_job.add_done_callback(lambda _: self._wake.set())

    def _interruption_note(self, task: Task) -> str:
        if task.kind != "player":
            return ""
        steps = "; ".join(task.progress[-5:]) or "nothing done yet"
        return (f"\n## Interrupted request\nYou were working on <{task.requester}> {task.instruction}\n"
                f"Progress so far: {steps}\nThe new message may change, extend or cancel that request.\n")

    # ---------------------------------------------------------- tool loop

    async def _run_task_safe(self, task: Task) -> None:
        try:
            await self.run_task(task)
        except asyncio.CancelledError:
            log.info("task cancelled: %s", task.label())
            raise
        except Exception as exc:  # noqa: BLE001
            if task.replied:
                # The player already got an answer; a late LLM hiccup isn't worth an apology.
                log.warning("task ended early after LLM error (%s): %s", exc.__class__.__name__, task.label())
                return
            log.exception("task failed: %s", task.label())
            self.memory.add("task", f"Task failed with an internal error: {exc}")
            if task.kind == "player" and not task.replied:
                await self._say("Sorry, my brain glitched for a sec. Try that again?", task)
        finally:
            self._last_activity = time.time()

    async def run_task(self, task: Task) -> str:
        log.info("▶ task [%s]", task.label())
        state = await self._fresh_state()
        state_text = self.adapter.format_state(state)
        memory_text = self.memory.render()

        system = prompts.system_prompt(persona=self.persona, game=self.adapter.display_name,
                                       name=self.name, owner=self.owner, game_notes=self.game_notes,
                                       nickname=self.cfg.agent.name)
        if task.kind == "player":
            user = prompts.PLAYER_TASK_TEMPLATE.format(
                state=state_text, memory=memory_text, interrupted=task.interrupted_context,
                sender=task.requester, message=task.instruction)
            max_steps = self.loop_cfg.get("max_steps_player_task", 25)
        elif task.kind == "reaction":
            user = prompts.REACTION_TASK_TEMPLATE.format(state=state_text, memory=memory_text, event=task.instruction)
            max_steps = 8
        else:
            user = prompts.AUTONOMOUS_TASK_TEMPLATE.format(state=state_text, memory=memory_text, goal=task.instruction)
            max_steps = self.loop_cfg.get("max_steps_autonomous", 12)

        messages: list[dict[str, Any]] = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        tools = self.registry.openai_tools()
        outcome = "ran out of steps"

        for step in range(max_steps):
            resp = await self.llm.chat(messages, tools)
            messages.append(resp.message)

            if not resp.tool_calls:
                # Plain text with no tool call = the model is done. Treat it as a chat reply
                # to the player if it never spoke.
                if resp.content and task.kind == "player" and not task.replied:
                    await self._say(resp.content, task)
                outcome = "done"
                break

            finished = False
            for call in resp.tool_calls:
                payload = await self.execute_tool(call, task)
                if call.name == "respond_to_player" and payload.get("ok"):
                    task.replied = True
                messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(payload, default=str)})
                if call.name == "task_complete":
                    finished = True
                    outcome = call.arguments.get("summary") or "done"
            if finished:
                break
            messages = trim_history(messages, self.loop_cfg.get("history_messages", 24))
        else:
            log.warning("task hit step limit (%d): %s", max_steps, task.label())
            if task.kind == "player":
                await self._say("That's taking me longer than expected, I'll stop here for now.", task)

        if task.kind == "player" and not task.replied and outcome not in ("done", "ran out of steps"):
            # The player should always hear back; use the completion summary.
            await self._say(outcome, task)
        log.info("■ task done [%s] -> %s", task.label(), outcome)
        if task.kind == "player":
            self.memory.add("task", f"Handled {task.requester}'s request '{task.instruction[:80]}': {outcome}")
        elif task.kind == "autonomous":
            self.memory.add("task", f"Own goal '{task.instruction[:80]}': {outcome}")
            self.planner.record_outcome(task.instruction, outcome)
        return outcome

    async def execute_tool(self, call: ToolCall, task: Task) -> dict[str, Any]:
        args_txt = json.dumps(call.arguments, separators=(",", ":"))
        if call.parse_error:
            log.info("  ✗ %s: %s", call.name, call.parse_error)
            return ToolResult(False, error=call.parse_error).to_payload()
        if not self.registry.has(call.name):
            return ToolResult(False, error=f"unknown tool '{call.name}'").to_payload()

        if call.name == "respond_to_player":
            res = await self._say(str(call.arguments.get("message", "")), task)
            return res.to_payload()
        if call.name == "get_game_state":
            state = await self._fresh_state()
            return {"ok": True, "result": self.adapter.format_state(state)}
        if call.name == "task_complete":
            return {"ok": True, "result": "task closed"}

        log.info("  → %s %s", call.name, args_txt)
        t0 = time.monotonic()
        res = await self.backend.execute(call.name, call.arguments)
        dt = time.monotonic() - t0
        summary = json.dumps(res.result, default=str) if res.ok else res.error
        log.info("  %s %s (%.1fs): %s", "✓" if res.ok else "✗", call.name, dt, (summary or "")[:240])
        task.progress.append(f"{call.name}({args_txt}) -> {'ok' if res.ok else 'failed'}: {(summary or '')[:120]}")
        payload = res.to_payload()
        try:
            state = await self._fresh_state()
            payload["status"] = self.adapter.format_status_line(state)
        except Exception:  # noqa: BLE001
            pass
        return payload

    async def _say(self, text: str, task: Task | None) -> ToolResult:
        text = " ".join(text.split())
        if not text:
            return ToolResult(False, error="empty message")
        text = await self.voice.voice(text)
        res = await self.outputs.say(text)
        if res.ok:
            log.info("  💬 %s", text)
            self.memory.chat_out(self.name, text)
            if task and task.requester:
                self.detector.note_reply(task.requester)
            elif self.owner:
                self.detector.note_reply(self.owner)
        return res

    async def _fresh_state(self) -> dict[str, Any]:
        state = await self.adapter.get_state()
        self._last_state = state
        self._last_state_at = time.time()
        return state


def trim_history(messages: list[dict[str, Any]], keep: int) -> list[dict[str, Any]]:
    """Keep system + task prompt + the last `keep` messages, never starting on an
    orphaned tool result."""
    head, tail = messages[:2], messages[2:]
    if len(tail) <= keep:
        return messages
    tail = tail[-keep:]
    while tail and tail[0].get("role") == "tool":
        tail = tail[1:]
    return head + tail
