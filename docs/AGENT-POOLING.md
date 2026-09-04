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
| `name`        | Pool name. `"default"` is used when no `pool` is specified in a dispatch task. |
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

When pooling is enabled and at least one delegate-able agent exists across all
configured pools, a `dispatch` built-in tool is registered.

**Single-pool schema** (the `pool` field is omitted to keep things simple):

```json
{
  "tasks": [
    { "agent": "search-agent", "task": "Find recent news about X" },
    { "agent": "analyst-agent", "task": "Summarize findings" }
  ]
}
```

**Multi-pool schema** (an optional `pool` field is added when multiple pools
are configured, defaulting to `"default"` when omitted):

```json
{
  "tasks": [
    { "agent": "search-agent", "pool": "default",  "task": "Find recent papers on X" },
    { "agent": "code-writer",  "pool": "coding",   "task": "Write a Python parser for the results" },
    { "agent": "code-reviewer","pool": "coding",   "task": "Review the parser for correctness" }
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
- The `agent` field is an enum of **all agents across all pools** so the model
  always has a complete, validated list. The `pool` field (when present) is an
  enum of pool names; mismatched `pool`/`agent` combinations are caught at
  runtime and reported as per-task errors.
- `pool` is optional and always defaults to `"default"` — single-pool configs
  require no changes when additional pools are added later.

## Live monitoring

While a batch is running (and afterwards, until the next batch), the TUI shows
a collapsible **Active Sub-Agents** tree above the chat log — one node per
dispatched task, labelled with the agent name, status (`running`/`done`/`error`),
and current activity. Clicking a node opens a full-screen `SubAgentViewScreen`
modal that replays that agent's streaming text, thinking, and tool calls in real
time via `agent.stream_sub_agent_run()` into a `SubAgentRun`; `Esc` or the
"Back to root" button returns to the main chat. Runs are kept in the tree for
review until the next `dispatch` call replaces them.

## Configuring multiple pools

The `agent-pools` key is a **list**, so you can declare as many named pools as
you like in a single `agents.yaml`. Each pool is independent — it has its own
`name`, `description`, and `agents` list — and `AgentPool` loads all of them
into its internal registry at startup (keyed by pool name).

```yaml
agent-pools:
  # ── Pool 1 ──────────────────────────────────────────────────────────────────
  - name: default
    description: |
      General-purpose pool used by the root agent for everyday research and
      analysis tasks.
    agents:
      - name: search-agent
        model: gemini-2.0-flash
        profile: search-agent
        backend:
          type: openai
          base_url: ${GCP_GATEWAY_URL}
          api_key: ${OLI_OPENAI_API_KEY}

      - name: analyst-agent
        model: claude-haiku-4-5
        profile: analyst
        backend:
          type: openai
          base_url: ${AWS_GATEWAY_URL}
          api_key: ${OLI_OPENAI_API_KEY}

  # ── Pool 2 ──────────────────────────────────────────────────────────────────
  - name: coding
    description: |
      Specialist pool for code-generation and review tasks. Agents here target
      a locally-hosted Ollama instance to keep code off external APIs.
    agents:
      - name: code-writer
        model: codellama:13b
        profile: coding
        backend:
          type: ollama
          base_url: http://localhost:11434

      - name: code-reviewer
        model: deepseek-coder:6.7b
        profile: coding
        backend:
          type: ollama
          base_url: http://localhost:11434

  # ── Pool 3 ──────────────────────────────────────────────────────────────────
  - name: research
    description: |
      High-capacity pool for long-running research tasks using larger, slower
      frontier models.
    agents:
      - name: deep-researcher
        model: gpt-4o
        profile: research
        backend:
          type: openai
          base_url: ${OPENAI_BASE_URL}
          api_key: ${OPENAI_API_KEY}
```

A few things worth noting:

- **Agent names must be unique within a pool**, but the same name can appear in
  different pools (e.g. a `search-agent` in both `default` and `research`).
- **Each pool is validated independently** — the `agent_pool_size` cap applies
  per-pool, not across all pools combined.
- **`AgentPool.select_agent(pool_name, agent_name)` and
  `AgentPool.list_agents(pool_name)`** accept the pool name as their first
  argument, so all registered pools are fully accessible programmatically.
- **The `dispatch` tool targets the correct pool per task** — each task item
  carries an optional `pool` field that is forwarded directly to
  `select_agent()`. Omitting `pool` routes the task to `"default"`.

---

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
