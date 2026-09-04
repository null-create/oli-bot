# oli

```text
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣀⣄⣀⡀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠐⣶⣾⣿⣿⣿⣿⣿⣶⡆⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⢰⡏⢤⡎⣿⣿⢡⣶⢹⣧⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⢸⣿⣶⣶⣇⣸⣷⣶⣾⣿⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⢨⣿⣿⣿⢟⣿⣿⣿⣿⣿⣧⡀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⢸⣿⣿⡏⣿⣿⣿⣿⣿⣿⣿⣿⡄⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠘⣿⣿⣿⣜⠿⣿⣿⣿⣿⣿⣿⣿⡄⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠐⣷⣿⡿⣷⣮⣙⠿⣿⣿⣿⣿⣿⡄⠀
⠀⠀⠠⢄⣀⡀⠀⠀⠀⠀⠀⠈⠫⡯⢿⣿⣿⣿⣶⣯⣿⣻⣿⣿⠀
⠀⠀⠤⢆⠆⠈⠉⠳⠤⣄⡀⠀⠀⠀⠙⢻⣿⣿⠿⠿⠿⢻⣿⠙⠇
⠠⠤⠀⣉⣁⣢⣄⣀⣀⣤⣿⠷⠦⠤⣠⡶⠿⣟⠀⠀⠀⠀⠻⡀⠀
⠀⠀⠔⠋⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠃⠃⠉⠉⠛⠛⠿⢷⡶⠀⠀

            ░██████   ░██ ░██
          ░██    ░██  ░██
          ░██     ░██ ░██ ░██
          ░██     ░██ ░██ ░██
          ░██     ░██ ░██ ░██
          ░██    ░██  ░██ ░██
            ░██████   ░██ ░██
```

A lightweight, terminal-based AI agent harness for local-first, vendor-agnostic setups: bundled/drop-in personas with scoped tool sets, MCP extensibility, and configurable agent pools the root agent can delegate to at runtime.

## Why oli

Most agent harnesses today sit at one of a few extremes: single-agent skill runners with no delegation story, programmatic multi-agent orchestration that depends on a frontier model writing its own orchestration code, or production infrastructure platforms built around always-on gateways and cloud providers. oli is built for a narrower, specific case: **an interactive terminal session, running local models by default, where sub-agents are configurable, declarative, and inspectable.**

Concretely, that means:

- **Local-model-first, vendor-agnostic by design.** Ollama is the default backend. OpenAI, HuggingFace, and local Transformers backends are supported as peers, not afterthoughts — and pool entries can mix all of them in a single run.
- **Declarative dispatch, not code-written orchestration.** Sub-agents are defined in `agents.yaml` and addressed through a single `dispatch` tool call. This is a deliberate reliability bet: tool-calling a fixed schema is something small local models handle far more consistently than authoring correct multi-agent orchestration code.
- **Portable, bundled personas.** Profiles pair a system prompt and permission manifest with drop-in `AGENTS.md`/`SKILLS.md` content, aiming for compatibility with the open Agent Skills spec rather than a bespoke format.

## Features

- **Multi-backend support** — Ollama, OpenAI, HuggingFace (remote or local), and Transformers (local GPU/CPU). Switch at runtime.
- **Agent profiles** — drop-in system prompts with permission manifests, base-profile inheritance, and auto-generated profiles via `/profile create`. Bundled profiles: `default`, `coder`, `reviewer`, `writer`, `planner`, `search-agent`, `analyst`.
- **Rich built-in tool set** — file ops, shell access, web search/fetch, Wikipedia/GitHub/arXiv search, Git, task tracking, reasoning scratchpad, notebook, and more. Sandbox-locked with shell allowlists, SSRF protection, and sensitive-file gating.
- **MCP integration** — add stdio or HTTP MCP servers at runtime for custom tools.
- **Agent pooling (optional)** — with `--use-pool`, the root agent can fan tasks out concurrently to vendor-agnostic sub-agents defined in [agents.yaml](agents.yaml) via a `dispatch` tool. Each pool entry binds a model _and_ a backend, so dispatch decisions are also compute-location decisions — a frontier model can plan while sensitive work stays on a local model, or a local root can fan out to faster remote SLMs for latency-sensitive tool calls.
- **Permission system** — write operations and sensitive reads require user approval. Session grants, workspace scoping, and profile-level allow/deny lists.
- **Streaming Markdown** responses in a Textual TUI, with in-app model/server/session/profile management.
- **OpenAI-compatible API server** — run the same agent harness behind `/v1/models` and `/v1/chat/completions` (streaming + non-streaming) so any workflow that speaks the OpenAI wire protocol (the `openai` Python SDK, curl, or plain REST) can drive the agent.

## Roadmap / areas of active exploration

These aren't shipped yet, but they're the directions we think are the most genuinely differentiated, and where we're focusing next:

- **Pool-scoped permissions.** Extend the permission manifest so dispatch itself is a permissioned action — a profile could require approval before it's allowed to hand work to a given pool entry, and manifests could declare which other profiles they're allowed to dispatch to at all.
- **Sensitivity-aware routing.** A per-agent `data_sensitivity` / `egress` field in `agents.yaml` (e.g. `local-only`, `redact-on-return`, `unrestricted`) that governs not just where a task runs, but what's allowed to flow back into a remote root's context — the actual enforcement mechanism behind the "frontier root, local subagent" privacy pattern.
- **Per-dispatch cost/latency telemetry.** A `/pool stats` command surfacing spend and time by agent over a session, useful specifically because oli's pools are expected to mix free local models with paid remote ones.

## Installation

Requires Python 3.11+. Install the package (this adds the `oli` and
`oli-server` entry points to your PATH):

```bash
pip install -e .        # editable dev install
# or, from PyPI once published:
pip install oli-bot
```

## Quick start

```bash
# Run (defaults to Ollama with offline mode on)
oli

# Run with OpenAI
OLI_BACKEND=openai OLI_OPENAI_API_KEY=sk-... oli

# Run with a profile
oli --profile search-agent
```

### Bundled profiles

| Profile        | Write? | Shell? | Web? | Best for                                   |
| -------------- | :----: | :----: | :--: | ------------------------------------------ |
| `default`      | ✅     | ✅     | ✅   | General-purpose tasks                      |
| `coder`        | ✅     | ✅     | ✅   | Software development end-to-end            |
| `reviewer`     | ❌     | ✅     | ❌   | Code review, quality analysis              |
| `writer`       | ✅     | ❌     | ✅   | Docs, READMEs, changelogs, prose           |
| `planner`      | ❌     | ❌     | ✅   | Roadmaps, task decomposition, saved plans  |
| `search-agent` | ❌     | ❌     | ✅   | Web research with structured JSON output   |
| `analyst`      | ❌     | ❌     | ✅   | Cross-source claim extraction and analysis |

See [docs/PROFILES.md](docs/PROFILES.md) for the full manifest schema, permission layering, and how to create your own.

### CLI flags

| Flag                   | Description                                                                                      |
| ---------------------- | ------------------------------------------------------------------------------------------------ |
| `--model`              | Model to use (inherits from server config if available)                                          |
| `--url`                | Ollama server URL (default `http://localhost:11434`)                                             |
| `--profile`            | Agent profile (default `default`)                                                                |
| `--resume-last`        | Resume the most recent session on startup (mutually exclusive with `-s`/`--load-session`)        |
| `-s`, `--load-session` | Load a specific session by UUID on startup (mutually exclusive with `--resume-last`)             |
| `--dry-run`            | Start in dry-run mode                                                                            |
| `--offline`            | Force offline mode ON (already the default)                                                      |
| `--no-offline`         | Start with offline mode OFF                                                                      |
| `--verify-offline`     | Startup diagnostic -- warn if outbound calls are configured                                      |
| `--use-pool`           | Enable agent pooling (root agent can dispatch tasks to sub-agents in [agents.yaml](agents.yaml)) |

Running with no session flags always starts a new session. On exit, the app
prints a hint (`Resume this session with: -s <uuid> or --resume-last`) so you
can pick up where you left off.

### In-app commands

| Command                                                  | Description                                                                              |
| -------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| `/help`                                                  | Show help                                                                                |
| `/models [name]`                                         | Pick or switch to a model                                                                |
| `/model large\|small`                                    | Switch between configured large/small model tiers                                        |
| `/model set-large\|set-small <name>`                     | Set per-server large/small model and switch                                              |
| `/config`                                                | Open the configuration screen                                                            |
| `/context`                                               | Show current server, model, profile                                                      |
| `/servers add\|list\|remove\|default\|switch\|use-model` | Manage Ollama servers                                                                    |
| `/mcp add\|list\|remove`                                 | Manage MCP servers                                                                       |
| `/mode [ask\|agent\|chat\|plan]`                         | Switch mode (ask=read-only, agent=all tools, chat=no tools, plan=research + save a plan) |
| `/profile list\|load\|create`                            | Manage agent profiles                                                                    |
| `/sessions list\|switch\|delete\|rename\|purge`          | Manage conversation sessions                                                             |
| `/workspace list\|set\|unset`                            | Manage workspace directory                                                               |
| `/offline`                                               | Toggle offline mode                                                                      |
| `/dry-run`                                               | Toggle dry-run mode                                                                      |
| `/clear`                                                 | Clear the conversation                                                                   |
| `/home`                                                  | Return to the home screen                                                                |
| `Ctrl+Q` / `Ctrl+L` / `Ctrl+Y`                           | Quit / Clear / Copy last message                                                         |

## Documentation

| Document                                       | Contents                                                                        |
| ---------------------------------------------- | ------------------------------------------------------------------------------- |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)   | Architectural overview of the application, permission, configurations, and more |
| [docs/CONFIGURE.md](docs/CONFIGURE.md)         | Full configuration reference, settings precedence, sessions, workspace          |
| [docs/TOOLS.md](docs/TOOLS.md)                 | Built-in tools, permission system, security, dry-run/offline modes, truncation  |
| [docs/BACKENDS.md](docs/BACKENDS.md)           | Backend setup (Ollama, OpenAI, HuggingFace, Transformers), model tier switching |
| [docs/PROFILES.md](docs/PROFILES.md)           | Profile structure, manifests, built-in profiles, creating and loading profiles  |
| [docs/AGENT-POOLING.md](docs/AGENT-POOLING.md) | Agent pooling configuration, parsing, and usage                                 |

## Docker

The Compose file runs the OpenAI-compatible API server in a container. The API server runs its own independent agent instance and does not connect to any separate TUI agent.

**Start the API server:**

```bash
docker-compose up --build
```

The API server listens on `localhost:9734`, mounts `./profiles` and `~/.config/oli` to persist state across restarts, and is ready to accept OpenAI-compatible chat completions requests.

**Run the TUI agent locally (optional):**

```bash
oli
```

The TUI agent is a separate, interactive terminal interface. It has its own independent agent instance and does not interact with the containerized API server. Use this if you prefer an interactive terminal session instead of or in addition to the API.

## API server

The agent harness can be exposed over an OpenAI-compatible REST API, so existing Python SDKs and HTTP callers can drive it without the TUI:

```bash
# From source (editable install)
oli-server

# Or configure endpoint via env
OLI_API_HOST=0.0.0.0 OLI_API_PORT=9734 oli-server
```

It listens on `0.0.0.0:9734` by default and serves:

| Endpoint                                          | Description                                  |
| ------------------------------------------------- | -------------------------------------------- |
| `GET /v1/models`                                  | List the active model                        |
| `POST /v1/chat/completions`                       | Non-streaming chat completion                |
| `POST /v1/chat/completions` with `"stream": true` | Server-sent-event (SSE) streaming completion |
| `GET /health`                                     | Liveness probe                               |

Conversations are **stateless** (like real OpenAI): each `/v1/chat/completions` request carries its full message history. The server holds a single process-private `Agent` instance (backend, tool registrations, MCP wiring) shared across requests, and serializes concurrent in-flight requests in-process. Because there is no human to prompt at permission time, the API auto-allows permission scopes for the current request; offline and dry-run gating from `AppConfig` still apply.

### curl

```bash
# List models
curl http://localhost:9734/v1/models

# Non-streaming completion
curl -X POST http://localhost:9734/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "minimax-m3:cloud", "messages": [{"role": "user", "content": "Hello"}]}'

# Streaming completion
curl -N -X POST http://localhost:9734/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "minimax-m3:cloud", "stream": true, "messages": [{"role": "user", "content": "Count to three"}]}'
```

### Python (`openai` SDK)

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:9734/v1", api_key="not-needed")

resp = client.chat.completions.create(
    model="minimax-m3:cloud",
    messages=[{"role": "user", "content": "What can you do?"}],
)
print(resp.choices[0].message.content)

# Streaming
stream = client.chat.completions.create(
    model="minimax-m3:cloud",
    messages=[{"role": "user", "content": "Write a short poem"}],
    stream=True,
)
for chunk in stream:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="")
```

The `openai` SDK needs an `api_key`; pass a placeholder — the server does not require one.

### API configuration

| Env var           | Default   | Description                                 |
| ----------------- | --------- | ------------------------------------------- |
| `OLI_API_HOST`    | `0.0.0.0` | Bind address                                |
| `OLI_API_PORT`    | `9734`    | Listen port                                 |
| `OLI_API_PROFILE` | `default` | Agent profile to load                       |
| `OLI_API_MODE`    | `agent`   | Agent mode (`agent`, `ask`, `plan`, `chat`) |

`max_tokens` / `temperature` request fields are accepted but best-effort: the agent tool loop reads them from `AppConfig` (configured via `OLI_*` env or `settings.json`).

## Development

```bash
pip install -e '.[dev]'
pytest
```

Tests live under `tests/` and cover: sub-agent scaffolding, config env-var precedence, session round-trip, permission matrix, truncation boundaries, security regressions, OpenAI-style tool-call flushing, and the OpenAI-compatible API server endpoints. `tests/conftest.py` clears stray `OLI_*` env vars so runs are hermetic.
