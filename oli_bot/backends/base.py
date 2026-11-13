from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Dict, List, Optional

from ..config import configs
from ..models import (
    Message,
    ModelResponse,
    TextChunk,
    ThinkingChunk,
    ToolCall,
    ToolCallChunk,
    UsageChunk,
)

logger = logging.getLogger(__name__)

# Resolved at import time from the AppConfig singleton so subclasses keep their
# current default-argument behavior after the split.
MAX_TOKENS = configs.max_tokens
TEMPERATURE = configs.temperature

StreamEvent = TextChunk | ToolCallChunk | ThinkingChunk | UsageChunk


def estimate_tokens(text: str) -> int:
    """Rough token estimate for providers that do not report usage.

    Mirrors the heuristic used by the OpenAI-compatible API layer (~4 chars per
    token), so numbers stay consistent everywhere estimation is needed.
    """
    return max(1, (len(text) + 3) // 4)


TOOL_CALL_PARSE_ERROR = (
    "Error: the tool-call arguments could not be parsed (invalid or truncated "
    "JSON). This usually means the model hit its output-token limit mid-call. "
    "Retry with a smaller payload — e.g. split large content across multiple "
    "writes — or raise the configured max_tokens."
)


def parse_tool_arguments(raw: Optional[str]) -> tuple[Dict[str, Any], Optional[str]]:
    """Parse a tool call's ``arguments`` JSON.

    Returns ``(parameters, error)``. On failure ``parameters`` is an empty dict
    and ``error`` holds an actionable message describing the truncation so the
    caller can surface it as a tool result rather than raising or dropping the
    call silently.
    """
    try:
        parsed = json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError):
        logger.warning("Failed to parse tool-call arguments JSON: %r", raw)
        return {}, TOOL_CALL_PARSE_ERROR
    if not isinstance(parsed, dict):
        return {}, TOOL_CALL_PARSE_ERROR
    return parsed, None


def tool_call_from_raw(call_id: str, name: str, raw: Optional[str]) -> "ToolCall":
    """Build a ``ToolCall`` from raw ``arguments`` JSON, capturing parse
    failures as ``parse_error`` so the agent can surface a retryable result
    instead of raising mid-stream."""
    params, err = parse_tool_arguments(raw)
    return ToolCall(
        id=call_id, name=name, description="", parameters=params, parse_error=err
    )


class ModelBackend(ABC):
    @abstractmethod
    async def generate(
        self,
        model: Optional[str],
        messages: List[Message],
        tools: Optional[List[Dict]] = None,
        max_tokens: int = MAX_TOKENS,
        temperature: float = TEMPERATURE,
    ) -> ModelResponse:
        """Generate a response from the model"""
        pass

    @abstractmethod
    async def stream_generate(
        self,
        messages: List[Message],
        tools: Optional[List[Dict]] = None,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream a response from the model, yielding TextChunk and ToolCallChunk events.

        ``max_tokens`` caps the number of tokens generated this round. When
        ``None`` each backend falls back to its import-time ``MAX_TOKENS``
        default (or the provider's own default, for OpenAI-compatible APIs).
        """
        pass


__all__ = [
    "ModelBackend",
    "MAX_TOKENS",
    "TEMPERATURE",
    "StreamEvent",
    "estimate_tokens",
    "parse_tool_arguments",
    "tool_call_from_raw",
    "TOOL_CALL_PARSE_ERROR",
    "logger",
    "Any",
    "AsyncIterator",
    "Dict",
    "List",
    "Optional",
]
