# API Server

[`oli_bot/api_server.py`](../oli_bot/api_server.py) exposes the same `Agent`
harness that powers the TUI over an OpenAI-compatible REST API plus a stateful
WebSocket. Any client that speaks the OpenAI wire protocol — the `openai` Python
SDK, `curl`, or a custom HTTP client — can drive the full tool-calling agent,
including built-in tools, MCP servers, profiles, and modes.

## Quick start

The `oli-server` console script starts the server:

```bash
oli-server
```

On startup it prints a banner with the resolved backend, model, mode, profile,
and URL. By default it listens on `0.0.0.0:9734`.

## Server configuration

The server uses the same `AppConfig` as the TUI (see
[CONFIGURE.md](CONFIGURE.md)) for backend, model, offline/dry-run, and profile
settings. Four fields are specific to the server:

| Setting (env var)                 | Default   | Description                                     |
| --------------------------------- | --------- | ----------------------------------------------- |
| `api_host` (`OLI_API_HOST`)       | `0.0.0.0` | Bind address                                    |
| `api_port` (`OLI_API_PORT`)       | `9734`    | Listen port                                     |
| `api_profile` (`OLI_API_PROFILE`) | `default` | Profile loaded at startup (mirrors `--profile`) |
| `api_mode` (`OLI_API_MODE`)       | `agent`   | Mode: `agent` / `ask` / `chat` / `plan`         |

Examples:

```bash
OLI_API_HOST=127.0.0.1 OLI_API_PORT=9000 OLI_API_MODE=ask oli-server
```

or via the `api_server` section of `~/.config/oli/settings.json`:

```json
{
  "api_server": {
    "host": "127.0.0.1",
    "port": 9000,
    "profile": "researcher",
    "mode": "ask"
  }
}
```

Note the server has no `--model` / `--url` CLI flags; set the backend and model
with the same `OLI_*` env vars / `settings.json` the TUI uses.

## Architecture

- **Stateless REST** — every `POST /v1/chat/completions` request carries the full
  `messages` history and is answered with a complete completion, mirroring real
  OpenAI semantics. The server does not persist conversation state between
  requests.
- **Shared agent** — a single process-private `Agent` (backend, tool
  registrations, MCP wiring, profile) is built at module import and reused
  across requests, so connections are never rebuilt per call.
- **Serialized runs** — the shared `Agent` is not concurrent-safe, so in-flight
  `Agent.process()` runs are serialized process-wide with a
  `threading.RLock`. Concurrent requests queue.
- **No human in the loop** — the permission confirm-callback auto-defaults to
  `"session"` for every scope (the REST equivalent of the TUI's "Allow for
  session"). Offline mode and dry-run gating from `AppConfig` still apply
  unchanged.

## Endpoints

### `GET /health`

Liveness probe. Returns `{"status": "ok"}`.

### `GET /v1/models`

Lists the single active model.

```bash
curl http://localhost:9734/v1/models
```

```json
{
  "object": "list",
  "data": [
    {
      "id": "oli-bot-<model>",
      "object": "model",
      "created": 1730000000,
      "owned_by": "oli"
    }
  ]
}
```

### `POST /v1/chat/completions`

Non-streaming completion:

```bash
curl http://localhost:9734/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "List the files in this repo"}]
  }'
```

Request body mirrors the OpenAI schema. `model` is accepted but optional; the
server always uses its configured model. Recognized fields:

- `messages` (required) — array of `{role, content, name?}` messages
- `stream` — `true` for SSE streaming
- `temperature`, `max_tokens`, `max_completion_tokens`, `top_p`, `stop`, `n` —
  accepted for compatibility (the agent loop applies its own config-driven
  sampler defaults)

`content` may be a plain string **or** a list of parts for multimodality:

```json
{
  "messages": [
    {
      "role": "user",
      "content": [
        { "type": "text", "text": "What is in this image?" },
        {
          "type": "image_url",
          "image_url": { "url": "data:image/png;base64,iVBORw0KGgo=..." }
        }
      ]
    }
  ]
}
```

`image_url` parts with `data:` URIs become `ImageAttachment`s and ride through the
tool loop for vision-capable backends. Non-`data:` image URLs degrade to a
textual `[image: <url>]` placeholder.

Non-streaming response:

```json
{
  "id": "chatcmpl-<hex>",
  "object": "chat.completion",
  "created": 1730000000,
  "model": "gpt-4o",
  "choices": [
    {
      "index": 0,
      "message": { "role": "assistant", "content": "The repo contains ..." },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 0,
    "completion_tokens": 42,
    "total_tokens": 42
  }
}
```

`usage` is an approximation (the agent tool loop does not surface exact prompt
counts); `completion_tokens` is a `~chars/4` estimate.

**Errors** are returned in OpenAI format with HTTP 500:

```json
{
  "error": {
    "message": "The agent produced no response.",
    "type": "server_error",
    "code": "empty_response"
  }
}
```

A failed run (backend error, tool error, empty reply) surfaces as
`code: "agent_error"`; an empty completion uses `code: "empty_response"`.

#### Streaming

Set `"stream": true`. The server returns `text/event-stream` SSE:

```bash
curl -N http://localhost:9734/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"stream": true, "messages": [{"role": "user", "content": "hi"}]}'
```

```
data: {"id":"chatcmpl-...","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant","content":""}}]}

data: {"id":"chatcmpl-...","choices":[{"index":0,"delta":{"content":"Hello"}}]}

...

data: {"id":"chatcmpl-...","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

Streaming errors cannot change the HTTP status (the stream has already begun), so
a failed run emits one final SSE frame whose payload is `{"error": {...}}` before
`data: [DONE]`.

#### OpenAI Python SDK

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:9734/v1", api_key="unused")

resp = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Explain the tool loop"}],
)
print(resp.choices[0].message.content)
```

Streaming works unchanged:

```python
stream = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Count to ten"}],
    stream=True,
)
for chunk in stream:
    if chunk.choices and chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="")
```

### `WS /v1/chat`

A stateful WebSocket for real-time browser UIs. The server maintains a
per-connection `messages` history, so a client sends each next user turn as
`{"content": "..."}` and receives every `AgentEvent` back as a typed JSON frame.
`{"action": "clear"}` resets the per-connection history.

The server sends a `{"type": "connected"}` frame on accept, then one frame per
inbound message:

| Frame type            | Payload                                                        |
| --------------------- | -------------------------------------------------------------- |
| `text_chunk`          | `{"text": "..."}` — streamed assistant text                    |
| `thinking`            | `{"text": "..."}` — model reasoning block                      |
| `tool_call_executing` | `{"name": "...", "parameters": {...}}`                         |
| `tool_call_result`    | `{"name": "...", "result": "..."}`                             |
| `assistant_response`  | `{"content": "..."}` — final assembled assistant text          |
| `usage`               | `{"prompt_tokens": ..., "completion_tokens": ..., ...}`        |
| `error`               | `{"message": "..."}`                                           |
| `done`                | `{"full_text": "..."}` — run finished                          |
| `cleared`             | `{}` — history reset (reply to `action: clear`)                |
| `sub_agent_started`   | `{"task_id", "agent_name", "pool_name", "task"}`               |
| `sub_agent_progress`  | `{"task_id", "agent_name", "activity", "status"}`              |
| `sub_agent_completed` | `{"task_id", "agent_name", "status", "full_text"}`             |
| `todo`                | `{"todos": [...]}` (root) or with `task_id`/`agent_name` (sub) |

Sub-agent activity is relayed live while a `dispatch` tool call is in flight:
frames carry the same shape as above (`text_chunk`, `tool_call_executing`,
…) with `task_id` and `agent_name` attached so the client can demux by run.
`todo` frames are emitted whenever `builtin__todowrite` updates a task list —
the root list has no `task_id`; a sub-agent's list adds `task_id`/`agent_name`.

Example client:

```python
import asyncio, json
import websockets

async def main():
    async with websockets.connect("ws://localhost:9734/v1/chat") as ws:
        print(await ws.recv())  # {"type": "connected", ...}
        await ws.send(json.dumps({"content": "List the todos"}))
        while True:
            frame = json.loads(await ws.recv())
            if frame["type"] == "done":
                break
            if frame["type"] == "text_chunk":
                print(frame["data"]["text"], end="")
            elif frame["type"] == "error":
                print("ERROR:", frame["data"]["message"])
    # clear with: await ws.send(json.dumps({"action": "clear"}))

asyncio.run(main())
```

Invalid JSON payloads, non-object payloads, and empty messages are answered with
`{"type": "error", "data": {"message": "..."}}` and the connection stays open.

### `GET/PUT /v1/config`

Management endpoints backing the browser UI's settings view. They read and write
`~/.config/oli/settings.json` through the same `SettingsManager` the TUI uses,
translating between the nested settings format and a flat key-per-`AppConfig`-
field shape (`openai_model`, `truncation_max_chars_small`, `api_port`, ...).

`GET /v1/config` returns the current flat config:

```bash
curl http://localhost:9734/v1/config
```

```json
{
  "backend": "ollama",
  "ollama_model": "llama3.1",
  "temperature": 0.7,
  "api_port": 9734
}
```

`PUT /v1/config` takes a partial flat dict, overlays only the recognized keys
onto the existing settings (so secrets and env-driven values the UI doesn't
touch are preserved), validates them by round-tripping through `AppConfig`, and
persists the result:

```bash
curl -X PUT http://localhost:9734/v1/config \
  -H 'Content-Type: application/json' \
  -d '{"temperature": 0.2, "max_tool_iterations": 10}'
```

On validation failure it returns HTTP 422 with `{"error": {"message":
"Invalid config: ..."}}`. Note the running agent is **not** rebuilt — restart the
server for config changes to take effect.

### `GET/POST /v1/mcp` and `PUT/DELETE /v1/mcp/{name}`

CRUD for the MCP server configuration persisted to `mcp_servers.json`, backing
the browser UI's MCP server management view. Operates on the shared
`MCPClientManager` (`app.state.agent.mcp_manager`); all responses return the
updated list of server configs.

`GET /v1/mcp` lists the configured servers:

```bash
curl http://localhost:9734/v1/mcp
```

```json
[
  {
    "name": "github",
    "transport": "stdio",
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-github"],
    "env": { "GITHUB_TOKEN": "..." },
    "url": ""
  }
]
```

`POST /v1/mcp` registers a new server (409 on duplicate name), `DELETE
/v1/mcp/{name}` removes one (404 if unknown), and `PUT /v1/mcp/{name}` updates
the server whose stored `name` matches the path parameter (404 if not found).
Unknown transports, missing `command` for `stdio`, and missing `url` for `http`
return HTTP 422. The request body is the same shape as the list entries above.

```bash
curl -X POST http://localhost:9734/v1/mcp \
  -H 'Content-Type: application/json' \
  -d '{"name": "filesystem", "command": "/usr/local/bin/mcp-fs"}'
```

## Behavior notes

- **Workspace** — the agent's workspace defaults to the process CWD unless the
  CWD is a sensitive path (see `is_sensitive_path` in
  [`sessions.py`](../oli_bot/sessions.py)). File and shell tools that touch paths
  outside the workspace are auto-approved in API mode but still respect
  offline/dry-run gating.
- **Modes** — in `ask` / `plan` modes only read-only tools are exposed (plus
  MCP limits per mode); in `chat` mode no tools are offered at all. The mode also
  shapes the system prompt, exactly as in the TUI.
- **Concurrency** — requests are serialized on a process-wide lock; there is no
  horizontal multiplexing. For concurrent consumers, run multiple `oli-server`
  processes.
- **Logging** — the server writes NDJSON to `AppConfig.log_file` via the shared
  logger in [`logger.py`](../oli_bot/logger.py).
