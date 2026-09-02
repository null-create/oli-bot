# oli

An extensible, terminal-based AI agent — Textual TUI + OpenAI-compatible REST
API on top of a shared harness, with MCP tool integration, a sandboxed
built-in tool set, and configurable Ollama / OpenAI / HuggingFace /
Transformers backends.

This file is the top-level orientation map. Depth lives under [docs/](docs/).

## Architecture

| Path | Purpose |
| --- | --- |
| [oli_bot/chat.py](oli_bot/chat.py) | Textual TUI app (`OliBot`). Owns command handling, session UI, the `#command-suggestions` autocomplete `ListView`, and (when pooling is enabled) the "Active Sub-Agents" `Tree`. Slash-command names come from the module-level `COMMANDS` tuple. |
| [oli_bot/api_server.py](oli_bot/api_server.py) | FastAPI app exposing the harness over an OpenAI-compatible REST API (`GET /v1/models`, `POST /v1/chat/completions` streaming + non-streaming, `GET /health`). Stateless from the caller's POV; a single process-private `Agent` is shared across requests and serialised with a `threading.RLock`. Auto-approves permissions (no human), but offline/dry-run still apply. |
| [oli_bot/agent.py](oli_bot/agent.py) | `Agent` — mode + system prompt owner; orchestrates the tool-calling loop and streams typed events (`TextChunk`, `ThinkingChunk`, `ToolCallChunk`, `ToolCallExecuting`, `ToolCallResult`, `StreamChunk`, `Error`, `Done`). Also hosts `sanitize_tool_history`, `stream_sub_agent_run`, and `AgentPool` (built from [oli_bot/agents.yaml](oli_bot/agents.yaml) when `--use-pool` is set). |
| [oli_bot/backends/](oli_bot/backends/) | Backend package — `ModelBackend` ABC, `OllamaBackend`, `OpenAIBackend`, `HuggingFaceBackend`, `TransformersBackend`, and the `create_model_backend()` factory. Also hosts the shared `_StreamingThinkParser` and per-backend message formatting (Ollama native `images`, OpenAI `image_url` or Bedrock-native blocks via `openai_vision_style`, textual placeholder for text-only backends). See [docs/BACKENDS.md](docs/BACKENDS.md). |
| [oli_bot/screens/](oli_bot/screens/) | All `ModalScreen` subclasses: `PermissionScreen`, `ConfirmScreen`, `ModelPickerScreen`, `ServerListScreen`, `MCPSetupScreen`, `SessionListScreen`, `WorkspaceListScreen`, `SubAgentViewScreen`, `ConfigScreen`, `InputPromptScreen`, plus `taglines.py` / `todo_widget.py`. |
| [oli_bot/models.py](oli_bot/models.py) | Shared dataclasses: `Message`, `ToolCall`, `ModelResponse`, `HostConfig`, `MCPServerConfig`, `ProfileData`, `SubAgentRun`, `ImageAttachment`, `TodoItem` / `TodoListState`, plus `AgentEvent` variants and the `AgentRole` enum. `Message.images` is in-memory only (dropped on session save). |
| [oli_bot/config.py](oli_bot/config.py) | `AppConfig` — `pydantic_settings.BaseSettings`. Env vars prefixed `OLI_`, plus `.env` support and `OLI_TRUNCATION_SMALL` / `_LARGE` aliases via `AliasChoices`. Module-level `configs = AppConfig()` singleton. See [docs/CONFIGURE.md](docs/CONFIGURE.md). |
| [oli_bot/settings.py](oli_bot/settings.py) | `SettingsManager` — load/save/merge `~/.config/oli/settings.json`; precedence `settings.json` > `OLI_*` env > SDK-standard env (`OPENAI_API_KEY`, `OPENAI_BASE_URL`, `HUGGINGFACE_API_KEY`, `HF_TOKEN`) > declared defaults. Empty API-key strings in JSON fall through to env. |
| [oli_bot/profiles/](oli_bot/profiles/) | `ProfileManifest` / `PermissionsManifest` (Pydantic) in `schema.py`; `ProfilePermissionEnforcer` (layered allow/deny glob patterns, base-profile inheritance, deny-overrides-allow) in `permissions.py`. Built-in profiles ship as sibling directories. |
| [oli_bot/profile_manager.py](oli_bot/profile_manager.py) | `ProfileManager` — profile CRUD, manifest loading, circular-dependency detection; delegates enforcement to `oli_bot/profiles/permissions.py`. |
| [oli_bot/mcp_client.py](oli_bot/mcp_client.py) | `MCPClientManager` — MCP server lifecycle (stdio/http via v2 `mcp.client.Client`, `mode="auto"` handshake), tool discovery + invocation, per-server tool-list cache, offline gating. Uses v2 snake_case fields (`Tool.input_schema`, `CallToolResult.is_error`/`structured_content`). |
| [oli_bot/tools/manager.py](oli_bot/tools/manager.py) | `BuiltinToolManager` — registration, profile + session permission gating, dry-run gating, offline gating, and `TruncationManager` post-processing. Awaits coroutine handlers. |
| [oli_bot/tools/](oli_bot/tools/) | Tool handlers: `files.py` (read/write/edit + `view_image` via Pillow), `directories.py` (glob/grep/list_directory/tree — filesystem work runs via `asyncio.to_thread` / `create_subprocess_exec`), `web.py` (search + fetch + specialised searches, all guarded by `_check_ssrf`), `shell.py` (allowlisted `run_command`; sandboxed `git`), `parsing.py` (`compare`), `memory.py` (`think`, `todowrite`, `notebook`), `truncation.py` (per-tier char budgets), `permissions.py` (sensitive-path detection). See [docs/TOOLS.md](docs/TOOLS.md). |
| [oli_bot/sessions.py](oli_bot/sessions.py) | `Session` (permission gating) + `ConversationStore` (per-server JSON persistence under `~/.config/oli/sessions/<server>/`) + `WorkspaceManager`. `save_session()` returns the (possibly new) id so callers can rebind after a corrupt-file rewrite. Persisted messages preserve `tool_call_id`; loads pass through `sanitize_tool_history` so poisoned histories self-heal. |
| [oli_bot/server_manager.py](oli_bot/server_manager.py) | `ServerManager` — multi-server lifecycle persisted to `ollama_hosts.json`, URL validation. |
| [oli_bot/logger.py](oli_bot/logger.py) | Centralised NDJSON file logging (rotating, 10 MB × 5) under `AppConfig.log_file`. Deliberately no console handler — stray writes would corrupt the Textual TUI. |

## Modes

Switched via `/mode`. Orthogonal to the active profile.

- **agent** *(default)* — all tools enabled; runs up to `max_tool_iterations` (default 25) streaming tool-calling rounds.
- **ask** — read-only built-in tools; MCP and write tools disabled.
- **chat** — no tools; single streamed response.
- **plan** — read-only tools plus `notebook` / `todowrite` and MCP; write tools disabled. An ephemeral system-prompt note (`oli_bot/agent.py:PLAN_MODE_NOTE`) instructs the model to save the finished plan via `notebook(action="set", page="plan-<name>")`, which persists to `notes/plan-<name>.md` (auto-incrementing on collision).

## Profiles

Profile directories live under [oli_bot/profiles/](oli_bot/profiles/) and require `AGENTS.md` + `profile.json`; `SKILLS.md` is optional and, when present, is appended after `AGENTS.md` in the system prompt. Load at startup with `--profile <name>` or at runtime with `/profile load <name>` (clears the conversation).

Built-in profiles:

| Profile | Purpose |
|---------|---------|
| `default` | General-purpose assistant with access to all built-in tools |
| `search-agent` | Web-research specialist — high recall/precision discovery with structured JSON output |
| `analyst` | Data-analyst specialist — extracts claims, triangulates sources, flags tensions |

`/profile create <name>` generates a fresh `AGENTS.md` under the loaded model, then leaves activation to a subsequent `/profile load`. Full detail (manifest fields, permission layering, base inheritance) is in [docs/PROFILES.md](docs/PROFILES.md).

## Agent pooling (optional)

Disabled by default. Enable with `--use-pool` or `OLI_USE_AGENT_POOL=true`. When on:

- `oli_bot/chat.py` builds an `AgentPool` from [oli_bot/agents.yaml](oli_bot/agents.yaml); `${VAR}` expansion in each agent's `backend.base_url` / `api_key` is handled by `agent._expand_env`. The `root-agent` / `root` entry is always skipped (not a delegate target).
- A `dispatch` built-in tool is registered; `oli_bot/chat.py:_dispatch_tasks` fans out via `asyncio.gather` (**never sequentially**), each sub-agent runs its own `Agent.process()` loop with the shared tool set (minus `dispatch`) and lock-serialised permission callback.
- Live `SubAgentRun` nodes populate the "Active Sub-Agents" `Tree`; clicking one opens `SubAgentViewScreen` to replay text/thinking/tool calls in real time. Results are aggregated (`## <agent>\n<result>`) and returned as a single tool result to the root.

See [docs/AGENT-POOLING.md](docs/AGENT-POOLING.md) for the `agents.yaml` schema, per-agent backend overrides, and mixed-vendor pool examples.

## Key flows

- **Session lifecycle** — new UUID by default; `-s`/`--load-session <uuid>` loads a specific one, `--resume-last` loads the newest for the active server (mutually exclusive). Auto-saved after every response; `/sessions` opens `SessionListScreen` for browse/switch/rename/delete. On exit `main()` prints the resume hint.
- **Tool loop** — `Agent._tool_loop` calls `backend.stream_generate()`, streaming text and thinking to the TUI while accumulating tool calls; after each round it calls `MCPClientManager.drain_builtin_attachments()` so `view_image` results ride into the next turn as a synthetic user-role `Message(images=...)` (kept **after** all `role=tool` messages so the assistant→tool block stays contiguous for `sanitize_tool_history`). If iterations are exhausted, `_final_stream` does one text-only pass to guarantee a reply.
- **Error paths** — a failed round yields `Error` for the UI then `Done(full_text="")`; the empty `full_text` deliberately trips the TUI's "skip empty assistant append" branch so error text never enters `self.messages` (prevents the Bedrock/Anthropic 400 poisoning loop). All `stream_generate()` implementations re-raise instead of swallowing.
- **Permission gating** — `BuiltinToolManager.call_tool()` runs the profile enforcer, then `Session.needs_permission()`, then (if needed) `confirm_callback(description)`; the TUI shows `PermissionScreen` and the callback returns `"once"`, `"session"`, or `"deny"`. Session grants persist per scope for the process lifetime. `glob`/`grep` targeting patterns like `.env*`, `*.pem`, `*secret*` trigger the `workspace_sensitive` scope even inside the workspace.
- **Built-in tool naming** — registered as `builtin__<name>` and dispatched by `MCPClientManager.call_tool`.
- **Settings round-trip** — runtime model changes (`/model set-large|set-small`, `/servers set-default-model`) persist back to `settings.json` via `OliBot._persist_model_to_settings`; the `/config` form pre-populates from effective runtime values (server overrides included) via `_sync_settings_from_runtime`. Backend construction honours `self.config` overrides so JSON beats env at startup.

For sequence diagrams and the full state machines, see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Security posture

- **Shell allowlist** — `run_command` accepts only allowlisted binaries (`git` is deliberately **not** on the list — the sandboxed `git` tool exposes only `status`/`diff`/`log`/`show`/`blame`). Pipelines and `&&` chains are allowed, but each segment is `shlex`-tokenised, its head must be in `ALLOWED_COMMANDS`, and per-command `DENIED_ARGS` / `DENIED_ARG_PREFIXES` block known escape hatches (`find -exec`/`-delete`, `sed -i`, `awk -f`/`-i`, `xargs`' inner command validated recursively, etc.). Output redirects to real files require an active workspace and must resolve inside it; input redirects, subshells, backticks, brace expansion, line continuations, and non-`\n` control characters are rejected. Once an interpreter is allowlisted, its sandbox is advisory by design.
- **SSRF protection** — `oli_bot/tools/web._check_ssrf` gates every outbound HTTP call (fetch, download, upload, search_github, extract_article, the URL branch of `view_image`, and the fixed Open Library / Hacker News endpoints). Non-`http(s)` schemes refused; loopback, link-local (including cloud-metadata `169.254.169.254`), private (RFC 1918), reserved, multicast, and unspecified addresses rejected after DNS resolution.
- **Sensitive-file gating** — `Session.needs_permission` requires approval for `read_file` on `.env*`, `.ssh/`, `.aws/`, `*.pem/.key/.crt`, and the other patterns in `_SENSITIVE_FILE_NAMES` / `_SENSITIVE_PATH_COMPONENTS`. Sensitive `glob`/`grep` patterns escalate to the `workspace_sensitive` scope.
- **Offline mode** — enabled by default; blocks web tools and MCP HTTP transports.
- **Dry-run mode** — when on, destructive tools return a preview without executing.

Full command groupings, deny-list rules, and the tool-by-tool permission matrix live in [docs/TOOLS.md](docs/TOOLS.md).

## Permission summary

- Always require permission: `write_file`, `edit_file`, `download_file`, `upload_file`.
- Require permission only for paths **outside** the workspace (or always, if no workspace is set): `read_file`, `view_image`, `glob`, `grep`, `list_directory`, `tree`, `run_command`, `git`. `glob`/`grep` also require permission for sensitive patterns inside the workspace.
- Never gated: `websearch`, `fetch`, `search_wikipedia` / `_github` / `_arxiv` / `_stackoverflow` / `_open_library` / `top_hacker_news_stories` / `extract_article`, `think`, `todowrite`, `notebook` — but every network tool still runs through `_check_ssrf`.

## Dependencies

Runtime deps are declared in [pyproject.toml](pyproject.toml) `[project.dependencies]`; dev-only tools (`pytest`, `pytest-asyncio`, `black`) live under `[project.optional-dependencies].dev` and install via `pip install -e '.[dev]'`. A legacy [requirements.txt](requirements.txt) mirror is kept for the Docker build. Grouped:

- **TUI + rendering** — `textual`, `rich`, `art`
- **Config** — `pydantic`, `pydantic-settings`, `python-dotenv`
- **Backends** — `ollama`, `openai`, `huggingface_hub`, `transformers`, `accelerate`
- **MCP + API** — `mcp` (v2 SDK — pulls in `httpx2`, `mcp-types`, `opentelemetry-api`), `fastapi`, `uvicorn[standard]`
- **Tools** — `Pillow` (image handling), `httpx` / `requests` / `aiohttp`, `beautifulsoup4`, `ddgs`, `wikipedia`, `arxiv`, `googlesearch_python`, `stackapi`, `search_engine_parser`, `gnews`, `newspaper4k`
- **Python < 3.11 only** — `exceptiongroup`

## Run

Install the package (adds `oli` and `oli-server` to PATH):

```bash
pip install -e .        # editable dev install
# or, from PyPI once published:
pip install oli-bot
```

```bash
oli [--model MODEL] [--url URL] [--profile PROFILE] \
    [--resume-last | -s UUID] \
    [--dry-run] [--offline | --no-offline] \
    [--verify-offline] [--use-pool]
```

| Flag | Purpose |
| --- | --- |
| `--model` | Model name to select at startup |
| `--url` | Ollama server URL (default `http://localhost:11434`) |
| `--profile` | Profile to load from `profiles/` (default `default`) |
| `--resume-last` | Auto-resume the most recent session for the active server (mutually exclusive with `-s`) |
| `-s`, `--load-session` | Load a specific session UUID (mutually exclusive with `--resume-last`) |
| `--dry-run` | Preview destructive actions without executing |
| `--offline` / `--no-offline` | Force offline mode on/off (default: on) |
| `--verify-offline` | Startup diagnostic — warn if any outbound calls are configured |
| `--use-pool` | Enable agent pooling (root can dispatch to sub-agents in [agents.yaml](oli_bot/agents.yaml)) |

Run the API server with `oli-server` (uvicorn on `0.0.0.0:8000`). Env vars: `OLI_API_HOST`, `OLI_API_PORT`, `OLI_API_PROFILE`, `OLI_API_MODE`. Both TUI and API can run side by side via `docker-compose up --build` (`api` + `agent` services share one image).

## Configuration

`AppConfig` fields are overridable via `OLI_*` env vars or a `.env` file. Effective precedence: `settings.json` > `OLI_*` env > SDK-standard env (`OPENAI_API_KEY`, `OPENAI_BASE_URL`, `HUGGINGFACE_API_KEY`, `HF_TOKEN`) > declared defaults. Empty API-key strings in `settings.json` fall through to env.

For the full setting table, per-backend model tiers, truncation aliases (`OLI_TRUNCATION_SMALL` / `_LARGE`), and quick examples, see [docs/CONFIGURE.md](docs/CONFIGURE.md).

## Testing

```bash
pip install -e '.[dev]'
pytest
```

Tests under `tests/` cover: `AgentPool` scaffolding (lookup, `${VAR}` expansion, `root-agent` exclusion, per-agent backend overrides, concurrent dispatch), config env-var precedence, session round-trip + `save_session` ID propagation, permission matrix (workspace scoping, sensitive files, `glob`/`grep` sensitive-pattern gating), truncation boundary preservation, security regressions (`git` not in `run_command` allowlist, `find -exec/-delete` blocked, SSRF for loopback/private/link-local/non-`http(s)`), `OpenAIBackend.stream_generate` tool-call flushing on all finish reasons, and the `api_server.py` OpenAI-compatible endpoints. `tests/conftest.py` scrubs `OLI_*` env vars so runs are hermetic.

## Code style

- Python 3.11+; no type-checker config yet (`typing` imports).
- `dataclass` for data types, ABC for backends, `@work` for async TUI tasks.
- Screen classes live in [oli_bot/screens/](oli_bot/screens/); [oli_bot/chat.py](oli_bot/chat.py) imports them.
- Minimal comments. `_underscore` private methods, `snake_case`.
- **Sync work called from async code must be wrapped in `asyncio.to_thread` or dispatched through `asyncio.create_subprocess_exec/_shell`.** No blocking `subprocess.run` / sync `httpx.get` / sync `wikipedia`/`arxiv` inside a handler awaited by the Textual event loop.

## Error surfacing

- **Backend errors** → `ModelResponse.error`; `Agent.process()` yields `Error` when `finish_reason == "error"`; TUI shows a red Assistant panel.
- **Streaming errors** → `Error` followed by `Done(full_text="")`; the empty payload keeps the failure out of `self.messages`.
- **MCP warnings** → collected on `MCPClientManager._warnings`; drained via `pop_warnings()` and shown as `notify(..., severity="warning")` toasts.
- **`except: pass` is forbidden** — log via `logger.warning()` / `logger.debug()` before swallowing.
- **Logging** — configured centrally in [oli_bot/logger.py](oli_bot/logger.py) via `setup_logging(log_path=configs.log_file)`. All loggers write to NDJSON only, always at `DEBUG`; `AppConfig.log_level` only gates the in-panel debug preview of tool results.

## Maintenance

**CRITICAL:** Keep this file updated whenever a major or significant change occurs (new modules, changed flows, added dependencies, altered conventions, or anything else deemed significant enough worth remembering).
**CRITICAL:** Keep the top level README.md file updated whenever a major or significant change occurs (new modules, changed flows, added dependencies, altered conventions, or anything else deemed significant enough worth documenting).
**CRITICAL:** Keep `oli_bot/profiles/default/AGENTS.md` and `oli_bot/profiles/default/SKILLS.md` in sync — the default profile must always reflect the current set of built-in tools and their usage guidance.
**CRITICAL:** Keep each profile's `AGENTS.md` and `SKILLS.md` in sync — SKILLS.md must complement the workflow and output format defined in AGENTS.md.

## Commands (in-app)

`/help`, `/models [name]`, `/model large|small`, `/model set-large|set-small <name>`, `/config`, `/context`, `/servers add|list|remove|default|switch|use-model`, `/mcp add|list|remove`, `/mode [ask|agent|chat|plan]`, `/profile list|load|create`, `/sessions [list|switch|delete|rename|purge]`, `/workspace list|set|unset`, `/offline`, `/dry-run`, `/clear`, `/home`, `Ctrl+Q`, `Ctrl+L`, `Ctrl+Y`
