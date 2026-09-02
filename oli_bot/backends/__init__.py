"""Model backend package.

Split out of the historical monolithic ``backend.py`` — see [AGENTS.md](../AGENTS.md).
``backend.py`` is retained as a re-export shim so existing imports
(``from backend import Message, OllamaBackend, _StreamingThinkParser``,
etc.) keep working.
"""

from __future__ import annotations

from ..models import (
    ImageAttachment,
    Message,
    ModelResponse,
    TextChunk,
    ThinkingChunk,
    ToolCall,
    ToolCallChunk,
)

from .base import MAX_TOKENS, TEMPERATURE, ModelBackend, StreamEvent
from .factory import create_model_backend
from .huggingface import HuggingFaceBackend
from .messages import (
    _append_image_placeholder_text,
    _bedrock_content_parts,
    _format_messages,
    _format_tools,
    _strip_none_values,
    _validate_message_content_blocks,
)
from .ollama import OllamaBackend
from .openai import OpenAIBackend
from .streaming import _StreamingThinkParser
from .transformers import TransformersBackend

__all__ = [
    # Public API
    "ModelBackend",
    "OllamaBackend",
    "OpenAIBackend",
    "HuggingFaceBackend",
    "TransformersBackend",
    "create_model_backend",
    "StreamEvent",
    "MAX_TOKENS",
    "TEMPERATURE",
    # Re-exported model types so `from backend import Message` keeps working
    "Message",
    "ModelResponse",
    "ToolCall",
    "TextChunk",
    "ToolCallChunk",
    "ThinkingChunk",
    "ImageAttachment",
    # Private helpers used by tests
    "_StreamingThinkParser",
    "_format_messages",
    "_format_tools",
    "_validate_message_content_blocks",
    "_bedrock_content_parts",
    "_append_image_placeholder_text",
    "_strip_none_values",
]
