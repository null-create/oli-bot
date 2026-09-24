# Backends

oli supports four model backends, selected via the `backend` setting (`OLI_BACKEND` env var):

| Backend        | Description                                                         |
| -------------- | ------------------------------------------------------------------- |
| `ollama`       | Local Ollama server (default)                                       |
| `openai`       | OpenAI API or any OpenAI-compatible endpoint                        |
| `huggingface`  | HuggingFace Inference API (remote) or TGI/vLLM (local)              |
| `transformers` | Local model inference via HuggingFace `transformers`                |

## Ollama

Connects to a local Ollama server. Default URL: `http://localhost:11434`.

| Setting              | Default                  | Env var                  |
| -------------------- | ------------------------ | ------------------------ |
| `ollama_base_url`    | `http://localhost:11434` | `OLI_OLLAMA_BASE_URL`    |
| `ollama_model`       | `ollama`                 | `OLI_OLLAMA_MODEL`       |
| `ollama_small_model` | `""`                     | `OLI_OLLAMA_SMALL_MODEL` |

Multiple Ollama servers can be configured via `/servers` and persisted to `ollama_hosts.json`. Switch between them with `/servers switch <name>`.

## OpenAI

Works with the OpenAI API or any compatible endpoint (Azure, local proxies, etc.).

| Setting              | Default                     | Env var                  |
| -------------------- | --------------------------- | ------------------------ |
| `openai_api_key`     | `""`                        | `OLI_OPENAI_API_KEY`     |
| `openai_base_url`    | `https://api.openai.com/v1` | `OLI_OPENAI_BASE_URL`    |
| `openai_model`       | `gpt-4o`                    | `OLI_OPENAI_MODEL`       |
| `openai_small_model` | `gpt-4o-mini`               | `OLI_OPENAI_SMALL_MODEL` |
| `openai_vision_style` | `openai`                   | `OLI_OPENAI_VISION_STYLE` |

`openai_vision_style` controls how the `view_image` tool serializes attachments for the OpenAI backend:

- `openai` (default) — standard `{"type": "image_url", "image_url": {"url": "data:..."}}` blocks. Works with native OpenAI, Azure OpenAI, LiteLLM, DeepSeek, Groq, OpenRouter, etc.
- `bedrock` — Bedrock-native `{"image": {"format": "png|jpeg|gif|webp", "source": {"bytes": "<base64>"}}}` blocks. Use when the OpenAI-compatible endpoint is actually a Kong/LiteLLM proxy fronting AWS Bedrock and the translator does not map `image_url` correctly (surfaces as `ContentBlock ... must set one of the following keys: text, image, toolUse, ...` 400s).

## HuggingFace

Supports two modes controlled by `huggingface_remote`:

- **Remote** (`huggingface_remote: true`) -- HuggingFace Inference API, requires `huggingface_api_key`.
- **Local** (`huggingface_remote: false`) -- connect to a TGI or vLLM server via `huggingface_base_url`.

| Setting                   | Default                                | Env var                       |
| ------------------------- | -------------------------------------- | ----------------------------- |
| `huggingface_base_url`    | `https://api-inference.huggingface.co` | `OLI_HUGGINGFACE_BASE_URL`    |
| `huggingface_model`       | `gpt-4o`                               | `OLI_HUGGINGFACE_MODEL`       |
| `huggingface_small_model` | `gpt-4o-mini`                          | `OLI_HUGGINGFACE_SMALL_MODEL` |
| `huggingface_api_key`     | `""`                                   | `OLI_HUGGINGFACE_API_KEY`     |
| `huggingface_remote`      | `false`                                | `OLI_HUGGINGFACE_REMOTE`      |

## Transformers

Runs models locally via the HuggingFace `transformers` library with lazy model loading, GPU/CPU support, and XML-tag-based tool calling. Requires `transformers`, `torch`, and `accelerate` packages (declared in `pyproject.toml`). A GPU is recommended but CPU is supported.

| Setting               | Default  | Env var                   |
| --------------------- | -------- | ------------------------- |
| `transformers_model`  | `""`     | `OLI_TRANSFORMERS_MODEL`  |
| `transformers_device` | `"auto"` | `OLI_TRANSFORMERS_DEVICE` |
| `transformers_dtype`  | `"auto"` | `OLI_TRANSFORMERS_DTYPE`  |

- `transformers_device`: `auto` (lets `accelerate` pick), `cuda`, or `cpu`.
- `transformers_dtype`: `auto`, `float16`, `bfloat16`, or `float32`.

## Model tier switching

Every backend supports a large/small model pair. At runtime:

- `/model large` or `/model small` -- switch between tiers
- `/model set-large <name>` or `/model set-small <name>` -- persist per-server model selection

See [CONFIGURE.md](CONFIGURE.md) for the full configuration reference.
