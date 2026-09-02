# Agent Pooling

Agent pooling lets a root/orchestrator agent delegate subtasks to a pool of
specialist sub-agents, each with its own model and backend. This is what turns
oli from a single-model assistant into a vendor-agnostic multi-agent system.

Agent pooling is **disabled by default**. Enable it with `--use-pool` at the
command line or by setting `OLI_USE_AGENT_POOL=true`.

## How it works

At startup, `chat.py` builds an `AgentPool` from `agents.yaml` (next to
`agent.py` in the repo root). Each named pool becomes a `dict` of `Agent`
instances keyed by agent name. When pooling is enabled, a `dispatch` built-in
tool is registered on the root agent. The root agent can call `dispatch` with a
batch of `{agent, task}` pairs; all tasks are run **concurrently**
(`asyncio.gather`, never sequentially) and the results are aggregated into a
single labeled string returned to the root agent's tool loop.

Each sub-agent runs its own full `Agent.process()` loop — its own model call,
its own tool-calling iterations — with the shared tool set (minus `dispatch`
itself, to prevent recursion) and the shared, lock-serialized permission
callback, so permission prompts behave consistently across agents.

## Enabling agent pooling

```bash
python chat.py --use-pool
```

or

```bash
OLI_USE_AGENT_POOL=true python chat.py
```

If `agents.yaml` is missing, the pool is skipped silently and the `dispatch`
tool is never registered.

## The agents.yaml schema

```yaml
agent-pools:
  - name: default
    description: |
      Used to route tasks to subagents by the root orchestration agent.
    agents:
      - name: search-agent
        model: nemotron3
        profile: search-agent
        backend:
          type: ollama
          base_url: http://localhost:11434
          api_key: ${OLLAMA_API_KEY}
```

Top level:

| Key           | Description                                                                                                      |
| ------------- | ---------------------------------------------------------------------------------------------------------------- |
| `agent-pools` | List of named pools. The top-level key is `agent-pools`; each pool entry is a `name`/`description`/`agents` map. |

Each pool:

| Key           | Description                                                       |
| ------------- | ----------------------------------------------------------------- |
| `name`        | Pool name. Only the `default` pool is wired for delegation in v1. |
| `description` | Free-form text describing the pool's purpose.                     |
| `agents`      | List of agent configs (1 to `agent_pool_size`, default max 5).    |

Each agent:

| Key       | Description                                                                                                                                              |
| --------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `name`    | Agent identifier used by the `dispatch` tool. Names `root-agent`/`root` are **excluded** — they represent the primary chat agent, not a delegate target. |
| `model`   | Model name for this agent. Required.                                                                                                                     |
| `profile` | Declared in the sample config as a hint, but the pool builder does not currently read it — sub-agents are built with the default profile.                |
| `backend` | Nested backend config — `type`, optional `base_url`, optional `api_key`. Required `type`.                                                                |

### Backend field

The `backend.type` selects the vendor via `create_model_backend()`. Supported
values: `openai`, `ollama`, `huggingface`, `transformers` (see
[BACKENDS.md](BACKENDS.md)).

The optional `backend.base_url` and `backend.api_key` are **per-agent
overrides** that take precedence over the global `configs.*` values when
provided. This lets each pooled agent target its own vendor/credentials
independent of the app-wide backend settings — e.g. a `search-agent` running on
a local Ollama server while the root agent talks to a hosted OpenAI-compatible
endpoint.

## Environment variable expansion

`${VAR}` references in `backend.base_url` and `backend.api_key` are expanded
against the process environment via `os.path.expandvars`. Both `${VAR}` and
`$VAR` forms work. Unset or empty expansions fall through to the backend's own
default (`configs.*`), so optional fields stay optional:

```yaml
backend:
  type: openai
  base_url: ${OPENAI_API_BASE_URL}
  api_key: ${OPENAI_API_KEY}
```

## Config limits

| Setting           | Default | Env var               | Description                                  |
| ----------------- | ------- | --------------------- | -------------------------------------------- |
| `use_agent_pool`  | `false` | `OLI_USE_AGENT_POOL`  | Enable agent pooling                         |
| `agent_pool_size` | `5`     | `OLI_AGENT_POOL_SIZE` | Max agents allowed per pool in `agents.yaml` |

A pool with zero agents, or more agents than `agent_pool_size`, raises a
`ValueError` at startup. Invalid entries (missing `name`, `model`, or
`backend.type`) are logged and skipped rather than aborting the build.

## The dispatch tool

When pooling is enabled and the `default` pool contains at least one
delegate-able agent, a `dispatch` built-in tool is registered. Its schema
exposes the pool's agent names as an enum:

```json
{
  "tasks": [
    { "agent": "search-agent", "task": "Find recent news about X" },
    { "agent": "analyst", "task": "Summarize findings" }
  ]
}
```

- Tasks run concurrently and never sequentially.
- Results are returned as one string, each agent's output under a `## <agent>`
  heading, in the order the batch was submitted.
- `dispatch` is removed from the tool set passed to sub-agents to prevent
  recursion, so sub-agents cannot re-dispatch.
- Per-agent errors are caught and reported as `Error: <message>` in that
  agent's section rather than failing the whole batch.

## Live monitoring

While a batch is running (and afterwards, until the next batch), the TUI shows
a collapsible **Active Sub-Agents** tree above the chat log — one node per
dispatched task, labelled with the agent name, status (`running`/`done`/`error`),
and current activity. Clicking a node opens a full-screen `SubAgentViewScreen`
modal that replays that agent's streaming text, thinking, and tool calls in real
time via `agent.stream_sub_agent_run()` into a `SubAgentRun`; `Esc` or the
"Back to root" button returns to the main chat. Runs are kept in the tree for
review until the next `dispatch` call replaces them.

## Example: mixed-vendor pool

```yaml
agent-pools:
  - name: default
    description: Mixed-vendor research pool.
    agents:
      - name: search-agent
        model: nemotron3
        backend:
          type: ollama
          base_url: http://localhost:11434
      - name: analyst
        model: gpt-4o
        backend:
          type: openai
          base_url: ${OPENAI_BASE_URL}
          api_key: ${OPENAI_API_KEY}
```

With this config, the root agent can fan out a web-research task to
`search-agent` (local Ollama) while a hosted OpenAI model drafts the analysis —
both dispatched in parallel via a single `dispatch` call.
