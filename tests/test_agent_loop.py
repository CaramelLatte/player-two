"""Agent tool-call loop against a scripted LLM and a fake game."""

import asyncio
from typing import Any

from actions.base import ActionBackend, ToolResult
from comms.messages import ChatMessage
from core.agent import Agent, Task
from core.config import load_config
from core.llm import LLMResponse, ToolCall
from game_state.base import GameAdapter
from tests.test_core import STATE


class FakeBackend(ActionBackend):
    name = "fake"
    supported_tools = frozenset({"collect_block", "give_item", "stop", "follow_player"})

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.chat: list[str] = []
        self.cancels = 0

    async def execute(self, tool, args):
        self.calls.append((tool, args))
        if tool == "collect_block":
            return ToolResult(True, {"mined": args.get("count", 1)})
        return ToolResult(True, "ok")

    async def cancel(self):
        self.cancels += 1

    async def send_chat(self, message):
        self.chat.append(message)
        return ToolResult(True, "sent")


class FakeAdapter(GameAdapter):
    game_id = "minecraft"
    display_name = "Minecraft"

    def __init__(self):
        self.backend = FakeBackend()

    async def start(self): ...
    async def stop(self): ...
    async def get_state(self): return STATE
    def format_state(self, state): return "state"
    def format_status_line(self, state): return "status"
    async def events(self):
        while True:
            await asyncio.sleep(3600)
            yield
    def action_backend(self): return self.backend
    def nearby_players(self, state): return [{"name": "CinnamonLatte", "distance": 2}]


def call(name, **args):
    return ToolCall(id=f"c_{name}", name=name, arguments=args, raw_arguments="{}")


class ScriptedLLM:
    def __init__(self, script: list[LLMResponse]):
        self.script = list(script)
        self.seen: list[list[dict[str, Any]]] = []

    async def chat(self, messages, tools=None, **kw):
        self.seen.append(list(messages))
        resp = self.script.pop(0)
        msg = {"role": "assistant", "content": resp.content}
        if resp.tool_calls:
            msg["tool_calls"] = [{"id": c.id, "type": "function", "function": {"name": c.name, "arguments": "{}"}}
                                 for c in resp.tool_calls]
        resp.message = msg
        return resp


def make_agent(script):
    cfg = load_config(overrides={"comms": {"sources": []}, "autonomy": {"enabled": False},
                                 "llm": {"persona_voice": {"enabled": False}}})
    adapter = FakeAdapter()
    llm = ScriptedLLM(script)
    return Agent(cfg, llm, adapter), adapter.backend, llm


async def test_player_task_executes_tools_and_completes():
    agent, backend, llm = make_agent([
        LLMResponse("", [call("respond_to_player", message="On it!")]),
        LLMResponse("", [call("collect_block", block_type="log", count=3)]),
        LLMResponse("", [call("give_item", player_name="CinnamonLatte", item_name="oak_log", count=3)]),
        LLMResponse("", [call("respond_to_player", message="Here you go"), call("task_complete", summary="gave logs")]),
    ])
    outcome = await agent.run_task(Task(kind="player", instruction="bring me 3 logs", requester="CinnamonLatte"))
    assert outcome == "gave logs"
    assert [c[0] for c in backend.calls] == ["collect_block", "give_item"]
    assert backend.chat == ["On it!", "Here you go"]
    # tool results were fed back to the model
    last = llm.seen[-1]
    assert any(m["role"] == "tool" and '"mined": 3' in m["content"] for m in last)


async def test_plain_text_answer_is_sent_as_chat():
    agent, backend, _ = make_agent([LLMResponse("Doing great, thanks!", [])])
    await agent.run_task(Task(kind="player", instruction="how are you", requester="CinnamonLatte"))
    assert backend.chat == ["Doing great, thanks!"]


async def test_bad_arguments_are_reported_not_executed():
    bad = ToolCall(id="x", name="collect_block", arguments={}, raw_arguments="{oops", parse_error="bad json")
    agent, backend, llm = make_agent([
        LLMResponse("", [bad]),
        LLMResponse("", [call("task_complete", summary="gave up")]),
    ])
    await agent.run_task(Task(kind="player", instruction="x", requester="CinnamonLatte"))
    assert backend.calls == []
    assert any(m["role"] == "tool" and "bad json" in m["content"] for m in llm.seen[-1])


async def test_chat_routing_and_stop_fast_path():
    agent, backend, _ = make_agent([])
    await agent.handle_chat(ChatMessage("CinnamonLatte", "PlayerTwo stop", "test"))
    assert backend.cancels == 1
    assert agent.pending and agent.pending.kind == "player"
    # duplicate from the other source is ignored
    agent.pending = None
    await agent.handle_chat(ChatMessage("CinnamonLatte", "PlayerTwo stop", "docker"))
    assert agent.pending is None
    # non-owner ignored
    await agent.handle_chat(ChatMessage("Stranger", "PlayerTwo give me diamonds", "test"))
    assert agent.pending is None


async def test_player_task_preempts_autonomous():
    agent, backend, _ = make_agent([])
    started = asyncio.Event()

    async def long_task():
        started.set()
        await asyncio.sleep(3600)

    agent.current = Task(kind="autonomous", instruction="wander")
    agent._current_job = asyncio.create_task(long_task())
    await started.wait()
    agent.submit(Task(kind="player", instruction="come here", requester="CinnamonLatte"))
    agent.submit(Task(kind="autonomous", instruction="ignored lower priority"))
    assert agent.pending.kind == "player"

    ran = []

    async def fake_run(task):
        ran.append(task)

    agent._run_task_safe = fake_run
    sched = asyncio.create_task(agent._scheduler_loop())
    await asyncio.sleep(0.1)
    sched.cancel()
    assert ran and ran[0].instruction == "come here"
    assert backend.cancels >= 1


async def test_silent_completion_falls_back_to_summary_chat():
    agent, backend, _ = make_agent([
        LLMResponse("", [call("collect_block", block_type="log", count=1)]),
        LLMResponse("", [call("task_complete", summary="Got you a log")]),
    ])
    await agent.run_task(Task(kind="player", instruction="get a log", requester="CinnamonLatte"))
    assert backend.chat == ["Got you a log"]


async def test_real_ingame_name_used_with_config_name_as_alias():
    cfg = load_config(overrides={"comms": {"sources": []}, "autonomy": {"enabled": False}})
    adapter = FakeAdapter()
    adapter.username = "LittleBagelByte"
    agent = Agent(cfg, ScriptedLLM([]), adapter)
    assert agent.name == "LittleBagelByte"
    # own messages (by real name) are ignored; both names trigger a response
    assert agent.detector.is_from_self(ChatMessage("LittleBagelByte", "hi", "docker"))
    assert agent.detector.decide(ChatMessage("CinnamonLatte", "PlayerTwo come here", "t")).addressed
    assert agent.detector.decide(ChatMessage("CinnamonLatte", "littlebagelbyte come here", "t")).addressed
