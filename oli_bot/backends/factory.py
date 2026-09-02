from __future__ import annotations

from ..config import configs

from .base import ModelBackend
from .huggingface import HuggingFaceBackend
from .ollama import OllamaBackend
from .openai import OpenAIBackend
from .transformers import TransformersBackend


def create_model_backend(
    url: str,
    backend_type: str,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
) -> ModelBackend:
    """Factory function to create the appropriate model backend.

    The ``url`` argument is only meaningful for the Ollama backend, which
    supports multi-server URL switching. For OpenAI and HuggingFace, the
    base URL is taken from ``config`` so that a stale Ollama server URL
    can't accidentally be sent to a hosted API (which would produce a
    404 like ``POST http://localhost:11434/chat/completions``).

    ``api_key`` and ``base_url`` are optional per-agent overrides (e.g. from
    ``agents.yaml``) that take precedence over the global ``configs.*``
    values when provided, allowing each pooled agent to target its own
    vendor/credentials independent of the app-wide backend settings.
    """

    if backend_type == "openai":
        return OpenAIBackend(
            api_key=api_key or configs.openai_api_key,
            base_url=base_url or configs.openai_base_url,
            model=model or configs.openai_model,
            vision_style=configs.openai_vision_style,
        )

    elif backend_type == "ollama":
        return OllamaBackend(
            base_url=base_url or url or configs.ollama_base_url,
            model=model or configs.ollama_model,
        )

    elif backend_type == "huggingface":
        return HuggingFaceBackend(
            model=model or configs.huggingface_model,
            api_key=api_key or configs.huggingface_api_key,
            base_url=base_url or configs.huggingface_base_url,
        )

    elif backend_type == "transformers":
        return TransformersBackend(
            model=model or configs.transformers_model,
            device=configs.transformers_device,
            dtype=configs.transformers_dtype,
            is_multi_model=configs.transformers_is_multi_model,
        )

    else:
        raise ValueError(f"Unknown model backend: {backend_type}")


__all__ = ["create_model_backend"]
