# Configuration

oli is configured via `AppConfig` in `config.py`, a `pydantic-settings` `BaseSettings` subclass. All settings are overridable with `OLI_*` environment variables or a `.env` file at the repo root.

## Precedence

Settings are resolved in this order (highest wins):

1. Explicit constructor kwargs (used by `SettingsManager` to overlay JSON)
2. `~/.config/oli/settings.json` (edited via `/config` at runtime)
3. `OLI_*` environment variables (or a loaded `.env`)
4. SDK-standard env vars (`OPENAI_API_KEY`, `OPENAI_BASE_URL`, `HUGGINGFACE_API_KEY`, `HF_TOKEN`)
5. Declared defaults

Empty API-key strings in `settings.json` are treated as unset and fall through to the env layer, so saving a blank key via `/config` does not clobber a real `.env` secret.

If `~/.config/oli/settings.json` does not exist, it is auto-created on first load as a snapshot of the resolved configuration: `OLI_*` env vars (or a loaded `.env`) where set, and `AppConfig` defaults from `config.py` otherwise. Values baked in at creation time then take precedence over later `.env` edits (per the ordering above).

## Settings

| Setting                      | Default                                | Env var                       | Description                                                                                  |
| ---------------------------- | -------------------------------------- | ----------------------------- | -------------------------------------------------------------------------------------------- |
| `backend`                    | `ollama`                               | `OLI_BACKEND`                 | Backend: `ollama`, `openai`, `huggingface`, `transformers`                                   |
| `openai_api_key`             | `""`                                   | `OLI_OPENAI_API_KEY`          | OpenAI API key                                                                               |
| `openai_base_url`            | `https://api.openai.com/v1`            | `OLI_OPENAI_BASE_URL`         | OpenAI-compatible base URL                                                                   |
| `openai_model`               | `gpt-4o`                               | `OLI_OPENAI_MODEL`            | OpenAI large model                                                                           |
| `openai_small_model`         | `gpt-4o-mini`                          | `OLI_OPENAI_SMALL_MODEL`      | OpenAI small model                                                                           |
| `openai_vision_style`        | `openai`                               | `OLI_OPENAI_VISION_STYLE`     | Vision content-block style: `openai` (default `image_url`) or `bedrock` (Bedrock-native `image` blocks for Kong/LiteLLM proxies fronting Bedrock)                                       |
| `ollama_base_url`            | `http://localhost:11434`               | `OLI_OLLAMA_BASE_URL`         | Ollama server URL                                                                            |
| `ollama_model`               | `ollama`                               | `OLI_OLLAMA_MODEL`            | Ollama large model                                                                           |
| `ollama_small_model`         | `""`                                   | `OLI_OLLAMA_SMALL_MODEL`      | Ollama small model                                                                           |
| `huggingface_base_url`       | `https://api-inference.huggingface.co` | `OLI_HUGGINGFACE_BASE_URL`    | HuggingFace Inference base URL                                                               |
| `huggingface_model`          | `gpt-4o`                               | `OLI_HUGGINGFACE_MODEL`       | HuggingFace large model                                                                      |
| `huggingface_small_model`    | `gpt-4o-mini`                          | `OLI_HUGGINGFACE_SMALL_MODEL` | HuggingFace small model                                                                      |
| `huggingface_api_key`        | `""`                                   | `OLI_HUGGINGFACE_API_KEY`     | HuggingFace API key                                                                          |
| `huggingface_remote`         | `false`                                | `OLI_HUGGINGFACE_REMOTE`      | Use remote HF Inference API (`true`) or local server (`false`)                               |
| `transformers_model`         | `""`                                   | `OLI_TRANSFORMERS_MODEL`      | Model name or local path for the Transformers backend                                        |
| `transformers_device`        | `"auto"`                               | `OLI_TRANSFORMERS_DEVICE`     | Device: `auto`, `cuda`, or `cpu`                                                             |
| `transformers_dtype`         | `"auto"`                               | `OLI_TRANSFORMERS_DTYPE`      | Data type: `auto`, `float16`, `bfloat16`, `float32`                                          |
| `use_agent_pool`             | `false`                                | `OLI_USE_AGENT_POOL`          | Enable agent pooling (`--use-pool`); root agent can dispatch tasks to agents.yaml sub-agents |
| `agent_pool_size`            | `5`                                    | `OLI_AGENT_POOL_SIZE`         | Max agents allowed per pool in agents.yaml                                                   |
| `max_tokens`                 | `2048`                                 | `OLI_MAX_TOKENS`              | Max tokens per generation                                                                    |
| `temperature`                | `0.7`                                  | `OLI_TEMPERATURE`             | Model temperature                                                                            |
| `max_retries`                | `3`                                    | `OLI_MAX_RETRIES`             | Max retries on API failure                                                                   |
| `retry_delay`                | `1.0`                                  | `OLI_RETRY_DELAY`             | Delay between retries (seconds)                                                              |
| `request_timeout`            | `30.0`                                 | `OLI_REQUEST_TIMEOUT`         | HTTP request timeout (seconds)                                                               |
| `max_messages`               | `100`                                  | `OLI_MAX_MESSAGES`            | Auto-prune chat history when message count exceeds this                                      |
| `max_tool_iterations`        | `25`                                   | `OLI_MAX_TOOL_ITERATIONS`     | Max tool-calling rounds per response                                                         |
| `stream_timeout`             | `240.0`                                | `OLI_STREAM_TIMEOUT`          | Seconds before streaming response times out                                                  |
| `model_filters`              | `""`                                   | `OLI_MODEL_FILTERS`           | Comma-separated model name exclusion filters                                                 |
| `truncation_max_chars_small` | `4000`                                 | `OLI_TRUNCATION_SMALL`        | Max chars for tool results under the small model tier                                        |
| `truncation_max_chars_large` | `100000`                               | `OLI_TRUNCATION_LARGE`        | Max chars for tool results under the large model tier                                        |
| `dry_run`                    | `false`                                | `OLI_DRY_RUN`                 | Preview destructive actions without executing                                                |
| `offline_mode`               | `true`                                 | `OLI_OFFLINE_MODE`            | Block network access for web tools and MCP servers                                           |
| `log_file`                   | `logs/backend.ndjson`                  | `OLI_LOG_FILE`                | Path for NDJSON backend log file                                                             |
| `log_level`                  | `INFO`                                 | `OLI_LOG_LEVEL`               | Logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR`                                           |

## Quick examples

```bash
# Override settings via env vars
OLI_MAX_MESSAGES=200 OLI_MAX_TOOL_ITERATIONS=10 python chat.py

# Use a .env file
echo 'OLI_BACKEND=openai' >> .env
echo 'OLI_OPENAI_API_KEY=sk-...' >> .env
python chat.py

# Edit settings at runtime
/config   # opens the TUI configuration screen
```

## Sessions

Conversations are automatically saved as JSON under `~/.config/oli/sessions/<server>/`. On startup without `--resume-last` or `--load-session`, if a previous session exists you'll be prompted to resume or start fresh. Manage sessions at runtime via `/sessions`.

## Workspace

The workspace controls which paths read tools can access without permission. Defaults to the current working directory on startup. Manage at runtime:

- `/workspace set <path>` -- set the workspace directory
- `/workspace unset` -- clear workspace (read tools always prompt)
- `/workspace list` -- browse recent workspaces

Recent workspaces are persisted to `~/.local/share/oli/workspaces.json`.
