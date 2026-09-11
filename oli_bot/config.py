"""Agent harness configuration — pydantic-settings with env override."""

import os
from typing import Optional

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# .env lives at the repo root (one level above the package) for dev installs;
# in a pip-installed deployment users override via OLI_* env vars instead.
_PKG_DIR = os.path.abspath(os.path.dirname(__file__))
ENV_FILE = os.path.join(os.path.dirname(_PKG_DIR), ".env")


class AppConfig(BaseSettings):
    """Configuration for the agent harness.

    Values are resolved from (highest precedence first):
      1. Explicit constructor kwargs (used by SettingsManager to overlay JSON).
      2. Environment variables prefixed with ``OLI_`` (or a loaded ``.env``).
      3. The defaults declared below.
    """

    model_config = SettingsConfigDict(
        env_prefix="OLI_",
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    # Backend selection: "ollama", "openai", "huggingface", or "transformers"
    backend: str = Field(default="ollama")

    # OpenAI configuration
    openai_api_key: str = Field(default="")
    openai_base_url: str = Field(default="https://api.openai.com/v1")
    openai_model: str = Field(default="")
    openai_small_model: str = Field(default="")
    # "openai" (default) emits standard image_url blocks. "bedrock" emits
    # Bedrock-native {"image": {"format", "source": {"bytes"}}} blocks for
    # OpenAI-compatible proxies that pass content through to Bedrock unchanged.
    openai_vision_style: str = Field(default="openai")
    # Optional headers to include in OpenAI API requests. This can be used to
    # pass additional headers required by certain OpenAI-compatible endpoints.
    openai_optional_headers: Optional[dict] = Field(default={})

    # Ollama
    ollama_base_url: str = Field(default="http://localhost:11434")
    ollama_model: str = Field(default="ollama")
    ollama_small_model: str = Field(default="")

    # Hugging Face
    huggingface_base_url: str = Field(default="https://api-inference.huggingface.co")
    huggingface_model: str = Field(default="")
    huggingface_small_model: str = Field(default="")
    huggingface_api_key: str = Field(default="")
    # True: HuggingFace Inference API (needs api_key); False: local TGI/vLLM at base_url.
    huggingface_remote: bool = Field(default=False)

    # Transformers (local)
    transformers_model: str = Field(default="")
    transformers_small_model: str = Field(default="")
    transformers_device: str = Field(default="auto")
    transformers_dtype: str = Field(default="auto")
    transformers_is_multi_model: bool = Field(default=False)

    # Voice mode (/voice command) — see oli_bot/voice.py
    voice_whisper_model: str = Field(default="base")
    voice_piper_model: str = Field(default="en_US-lessac-medium.onnx")
    # Sample rate required by WebRTC VAD
    voice_sample_rate: int = Field(default=16000)
    # WebRTC VAD constraint: 10 | 20 | 30
    voice_frame_duration_ms: int = Field(default=30)
    # 0–3; higher = more aggressive noise rejection
    voice_vad_aggressiveness: int = Field(default=2, ge=0, le=3)
    # Stop recording after this many ms of consecutive silence
    voice_silence_timeout_ms: int = Field(default=800, gt=0)
    # Hard cap to prevent runaway recordings
    voice_max_record_seconds: int = Field(default=15, gt=0)

    # General Agent configs
    use_agent_pool: bool = Field(default=False)
    agent_pool_size: int = Field(default=5)
    max_tokens: int = Field(default=2048)
    temperature: float = Field(default=0.7)
    max_retries: int = Field(default=3)
    retry_delay: float = Field(default=1.0)
    request_timeout: float = Field(default=30.0)
    max_messages: int = Field(default=100)
    max_tool_iterations: int = Field(default=25)
    stream_timeout: float = Field(default=240.0)
    model_filters: str = Field(default="")
    profiles_dir: str = Field(default="profiles")
    logs_dir: str = Field(default="logs")

    # Tool result truncation
    truncation_max_chars_small: int = Field(
        default=4000,
        validation_alias=AliasChoices(
            "truncation_max_chars_small",
            "OLI_TRUNCATION_SMALL",
            "OLI_TRUNCATION_MAX_CHARS_SMALL",
        ),
    )
    truncation_max_chars_large: int = Field(
        default=100000,
        validation_alias=AliasChoices(
            "truncation_max_chars_large",
            "OLI_TRUNCATION_LARGE",
            "OLI_TRUNCATION_MAX_CHARS_LARGE",
        ),
    )

    # Dry-run mode: preview destructive actions without executing
    dry_run: bool = Field(default=False)

    # Offline mode: block network access for web tools and MCP
    offline_mode: bool = Field(default=True)

    # Logging configuration
    log_level: str = Field(default="INFO")
    log_file: str = Field(default="logs/backend.ndjson")

    # API Server configuration
    api_host: str = Field(default="0.0.0.0")
    api_port: int = Field(default=8000)
    api_profile: str = Field(default="default")
    api_mode: str = Field(default="agent")


# Global configuration instance
configs = AppConfig()
