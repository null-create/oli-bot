from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Dict, List, Optional

from ..config import configs
from ..models import (
    Message,
    ModelResponse,
    TextChunk,
    ThinkingChunk,
    ToolCallChunk,
)

logger = logging.getLogger(__name__)

# Resolved at import time from the AppConfig singleton so subclasses keep their
# current default-argument behavior after the split.
MAX_TOKENS = configs.max_tokens
TEMPERATURE = configs.temperature

StreamEvent = TextChunk | ToolCallChunk | ThinkingChunk


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
    ) -> AsyncIterator[StreamEvent]:
        """Stream a response from the model, yielding TextChunk and ToolCallChunk events"""
        pass


__all__ = [
    "ModelBackend",
    "MAX_TOKENS",
    "TEMPERATURE",
    "StreamEvent",
    "logger",
    "Any",
    "AsyncIterator",
    "Dict",
    "List",
    "Optional",
]
