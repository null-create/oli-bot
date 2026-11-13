"""Token counting helpers shared by the agent, TUI, and API.

Uses ``tiktoken`` for models it recognises (with a generic BPE fallback for
unknown models) and falls back to the historical ~4 chars/token heuristic when
no encoding can be loaded. Counts are marked ``estimated`` so callers can show
a ``~`` prefix.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Iterable, Optional, Tuple

from .models import Message

logger = logging.getLogger(__name__)

# Approximate framing overhead per chat message (role separators, etc.).
_MESSAGE_OVERHEAD = 4
# Rough per-image cost for vision models when the tokenizer cannot see pixels.
_IMAGE_OVERHEAD = 256
_GENERIC_ENCODINGS = ("o200k_base", "cl100k_base")


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token) used as a final fallback."""
    return max(1, (len(text) + 3) // 4)


@lru_cache(maxsize=16)
def _resolve_encoding(model: str):
    """Return a tiktoken encoding for ``model``, or ``None`` if unavailable.

    The result (including ``None``) is cached so an unknown model or a failed
    encoding download does not retry on every checkpoint.
    """
    try:
        import tiktoken
    except Exception as e:  # pragma: no cover - tiktoken is a declared dependency
        logger.debug("tiktoken unavailable (%s); using heuristic token counts", e)
        return None

    try:
        return tiktoken.encoding_for_model(model)
    except Exception as e:
        logger.debug(
            "tiktoken has no encoding for model %r (%s); using generic BPE",
            model,
            e,
        )

    for name in _GENERIC_ENCODINGS:
        try:
            return tiktoken.get_encoding(name)
        except Exception as e:
            logger.debug("tiktoken encoding %s unavailable: %s", name, e)
    return None


def count_text_tokens(text: str, model: Optional[str] = None) -> Tuple[int, bool]:
    """Count tokens in ``text``.

    Returns ``(count, estimated)``. ``estimated`` is ``False`` only when a real
    tokenizer produced the count.
    """
    if not text:
        return 0, False
    encoding = _resolve_encoding(model or "")
    if encoding is not None:
        try:
            return len(encoding.encode(text, disallowed_special=())), False
        except Exception as e:
            logger.debug("tiktoken encode failed (%s); using heuristic", e)
    return estimate_tokens(text), True


def _message_text(message: Message) -> str:
    parts = [message.role or "", message.content or ""]
    if message.name:
        parts.append(message.name)
    if message.tool_call_id:
        parts.append(message.tool_call_id)
    if message.tool_calls:
        try:
            parts.append(
                json.dumps(message.tool_calls, ensure_ascii=False, default=str)
            )
        except (TypeError, ValueError):
            parts.append(str(message.tool_calls))
    return "\n".join(parts)


def count_message_tokens(
    messages: Iterable[Message], model: Optional[str] = None
) -> Tuple[int, bool]:
    """Count the prompt tokens for a list of messages.

    Adds a small per-message framing overhead (role separators etc.) and a
    fixed allowance per image attachment.
    """
    total = 0
    estimated = False
    for message in messages:
        count, est = count_text_tokens(_message_text(message), model)
        total += count + _MESSAGE_OVERHEAD
        if message.images:
            total += _IMAGE_OVERHEAD * len(message.images)
        estimated = estimated or est or bool(message.images)
    return total, estimated


class ContextTokenTracker:
    """Incremental count of the tokens the next/current request will send."""

    def __init__(self, messages: Iterable[Message], model: Optional[str] = None):
        self.model = model
        self._prompt_tokens, self._estimated = count_message_tokens(messages, model)
        self._inflight_text = ""
        self._exact_completion: Optional[int] = None

    def add_message(self, message: Message) -> None:
        """Add a message that was appended to the conversation history."""
        count, est = count_text_tokens(_message_text(message), self.model)
        self._prompt_tokens += count + _MESSAGE_OVERHEAD
        if message.images:
            self._prompt_tokens += _IMAGE_OVERHEAD * len(message.images)
        self._estimated = self._estimated or est or bool(message.images)

    def add_inflight(self, text: str) -> None:
        """Accumulate model output that is not yet part of the history."""
        if not text:
            return
        self._inflight_text += text
        self._exact_completion = None

    def clear_inflight(self) -> None:
        """Drop in-flight output (it has been folded into the history)."""
        self._inflight_text = ""
        self._exact_completion = None

    def set_exact(
        self, prompt_tokens: int, completion_tokens: int, estimated: bool
    ) -> None:
        """Adopt provider-reported counts for the completed model call.

        A provider that omits ``prompt_tokens`` (some OpenAI-compatible
        servers report ``0`` while still reporting completions) must not wipe
        out the locally counted context; keep the local count and flag the
        snapshot as estimated in that case.
        """
        if prompt_tokens > 0:
            self._prompt_tokens = prompt_tokens
            self._estimated = estimated
        else:
            self._estimated = True
        self._exact_completion = completion_tokens

    def snapshot(self) -> Tuple[int, int, int, bool]:
        """Return ``(prompt, completion, total, estimated)`` for the current context."""
        if self._exact_completion is not None:
            completion = self._exact_completion
            estimated = self._estimated
        else:
            completion, est = count_text_tokens(self._inflight_text, self.model)
            estimated = self._estimated or est
        return (
            self._prompt_tokens,
            completion,
            self._prompt_tokens + completion,
            estimated,
        )


__all__ = [
    "ContextTokenTracker",
    "count_message_tokens",
    "count_text_tokens",
    "estimate_tokens",
]
