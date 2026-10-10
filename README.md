# Player Two

An autonomous AI companion that plays games alongside you. It runs its own game
client, reads game state, acts through high-level tools chosen by a local LLM,
takes instructions from in-game chat, and does its own thing when you don't need it.

The current target is **Minecraft (Java Edition)** through Mineflayer. See
[overview.txt](overview.txt) for the full vision.

## How it works

```
 in-game chat ──► comms/ (docker log parser + bot chat, dedup, "is this for me?")
                     │
                     ▼
               core/agent.py ── scheduler: player > reaction (got hurt) > autonomous goal
                     │                 (a new player message pre-empts whatever is running)
                     ▼
        LLM tool-call loop (LM Studio, OpenAI-compatible)
          state text + memory + instruction ──► tool call ──► result ──► next call …
                     │
                     ▼
     actions/ (universal tool specs) ──► MineflayerBackend ──HTTP──► Node bridge ──► Minecraft
                                                                         ▲
     game_state/ (adapter, state formatting) ◄───────── state & events ──┘
```

| Directory | What's in it |
|---|---|
| `core/` | LLM client, agent loop and scheduler, goal planner, prompts, memory, config |
| `game_state/` | `GameAdapter` interface; `minecraft/` holds the adapter, formatter, and Node bridge |
| `actions/` | Universal `ToolSpec`s, the registry, the Mineflayer backend, and the `uinput/` scaffold |
| `comms/` | Container log parser, chat sources, addressee detection, dedup, output channels |
| `config/` | `settings.yaml`, `games/minecraft.yaml`, `persona.md` |
| `scripts/` | Dev helpers: a throwaway test server and a fake player |
| `tests/` | Unit tests, including the agent loop against a scripted LLM |

### Tools available to the LLM (Minecraft)

`respond_to_player`, `get_game_state`, `task_complete`, `move_to_position`,
`go_to_player`, `follow_player`, `stop`, `go_to_surface`, `collect_block`,
`mine_block`, `find_blocks`, `place_block`, `craft_item`, `smelt_item`,
`equip_item`, `eat`, `give_item`, `drop_item`, `attack_entity`, `sleep_in_bed`.

High-level tools do the fiddly parts themselves. `collect_block` finds, walks to,
mines, and picks up blocks. `craft_item` uses or places a crafting table and
names any missing ingredients. `smelt_item` places a furnace, adds fuel, and
waits for the output.

### When does it respond?

A message from the owner counts as addressed to the agent if any of these hold:

* it mentions the agent's name or an alias (`PlayerTwo`, `player two`, `p2`);
* it's a whisper (`/msg PlayerTwo ...`);
* the agent replied to that player within the last 45s (follow-up);
* it says "you" and no other player is nearby.

Chat comes from the container log **and** the bot's own client. Duplicates are
dropped. Saying "stop" halts the bot right away, before the LLM finishes thinking.

## Setup

```bash
# Python
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
# Node bridge
(cd game_state/minecraft/bridge && npm install)
```

Then edit `config/settings.yaml` (owner, models) and `config/games/minecraft.yaml`
(server host/port, container name, auth).

## Running

```bash
.venv/bin/python main.py --check        # connect, print what the bot sees, exit
.venv/bin/python main.py                # run the companion
.venv/bin/python main.py --no-autonomy  # only act when spoken to
```

Override any setting from the CLI. A `game.` prefix targets the game config:

```bash
.venv/bin/python main.py --set game.server.port=25566 --set game.server.auth=offline --set llm.model=openai/gpt-oss-120b
```

Logs are written to `logs/player-two.log` (agent) and `logs/bridge.log` (bot).

### Microsoft login (online-mode servers)

With `auth: microsoft`, the first run prints a device-code prompt to the console,
for example `Open https://microsoft.com/link and enter code XXXX`. Sign in with
**the agent's own Minecraft account**. Tokens are cached in
`game_state/minecraft/bridge/.auth/`, so you only do this once.

### Development server

```bash
scripts/test_server.sh up                       # offline 26.1 server on :25566
.venv/bin/python main.py --set game.server.port=25566 --set game.server.auth=offline --set game.server.container=p2_test_mc
node scripts/fake_player.js --port 25566 "PlayerTwo come here" "+30" "thanks"
scripts/test_server.sh rcon "give PlayerTwo oak_log 8"
scripts/test_server.sh down
```

### Tests

```bash
.venv/bin/python -m pytest -q
```

## Models

| Setting | Default | Why |
|---|---|---|
| `llm.model` | `openai/gpt-oss-20b` | Runs the action loop. About 4s per step; handles multi-step crafting chains. |
| `llm.planner_model` | `openai/gpt-oss-120b` | Picks autonomous goals. Slower, but its judgement matters more than speed here. |
| `llm.persona_voice` | off (`skyfall-31b-v4w`) | Optionally rewrites chat lines in character. Slow. |

With gpt-oss-120b as the action model, each step takes 15–30s. LM Studio
sometimes rejects a gpt-oss sample with "does not match the expected format";
the client resamples automatically.

## Adding a game

1. `config/games/<game>.yaml`
2. A `GameAdapter` in `game_state/<game>/` (state, formatting, events), registered in `game_state/registry.py`
3. An `ActionBackend` in `actions/` that implements the universal tools it can support.
   For games without an API this will be the `actions/uinput/` backend.
4. A chat log parser in `comms/log_parser.py`

## Extension points

* **Voxta voice:** add an `OutputChannel` in `comms/outputs.py`, plus a chat
  source that pushes transcribed speech. `core/persona.py` is where Voxta
  personas would replace `persona.md`.
* **Server lifecycle:** set `server_api.base_url` and `auto_start` to have the
  agent start the game server through your API.
