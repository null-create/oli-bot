from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Dict, List, Optional

from ollama import AsyncClient as OllamaAsyncClient, ChatResponse as OllamaChatResponse

from ..models import (
    Message,
    ModelResponse,
    TextChunk,
    ThinkingChunk,
    ToolCall,
    ToolCallChunk,
)

from .base import MAX_TOKENS, TEMPERATURE, ModelBackend, StreamEvent
from .messages import _format_messages, _format_tools
from .streaming import _StreamingThinkParser

logger = logging.getLogger(__name__)


class OllamaBackend(ModelBackend):
    def __init__(
        self,
        model: str,
        base_url: str = "http://localhost:11434",
    ):
        self.model = model
        self.client = OllamaAsyncClient(host=base_url)
        self.base_url = base_url

    def set_base_url(self, url: str) -> None:
        self.base_url = url
        self.client = OllamaAsyncClient(host=url)

    async def generate(
        self,
        model: Optional[str],
        messages: List[Message],
        tools: Optional[List[Dict]] = None,
        max_tokens: int = MAX_TOKENS,
        temperature: float = TEMPERATURE,
    ) -> ModelResponse:
        logger.debug(
            "Generating response with %s: model=%s, messages=%s, tools=%s",
            self.__class__.__name__,
            model or self.model,
            messages,
            tools if tools else [],
        )

        try:
            kwargs: Dict[str, Any] = {
                "model": model or self.model,
                "messages": _format_messages(messages, image_style="ollama"),
                "options": {"num_predict": max_tokens, "temperature": temperature},
            }
            if tools:
                kwargs["tools"] = _format_tools(tools)
            response: OllamaChatResponse = await self.client.chat(**kwargs)

            tool_calls = []
            if response.message.tool_calls:
                tool_calls = [
                    ToolCall(
                        id=f"call_{i}",
                        name=tc.function.name,
                        description="",
                        parameters=dict(tc.function.arguments),
                    )
                    for i, tc in enumerate(response.message.tool_calls)
                ]

            return ModelResponse(
                content=response.message.content or "",
                tool_calls=tool_calls,
                finish_reason="stop",
            )
        except Exception as e:
            logger.exception(
                "Error in %s.generate: %s", self.__class__.__name__, str(e)
            )
            return ModelResponse(
                content="", tool_calls=[], finish_reason="error", error=str(e)
            )

    async def stream_generate(
        self,
        messages: List[Message],
        tools: Optional[List[Dict]] = None,
    ) -> AsyncIterator[StreamEvent]:
        try:
            kwargs: Dict[str, Any] = {
                "model": self.model,
                "messages": _format_messages(messages, image_style="ollama"),
                "options": {"num_predict": MAX_TOKENS, "temperature": TEMPERATURE},
                "stream": True,
            }
            if tools:
                kwargs["tools"] = _format_tools(tools)
            stream = await self.client.chat(**kwargs)
            yielded = False
            parser = _StreamingThinkParser()
            async for chunk in stream:
                thinking_field = getattr(chunk.message, "thinking", None)
                if thinking_field:
                    yield ThinkingChunk(thinking_field)
                    yielded = True
                if chunk.message.content:
                    for kind, text in parser.feed(chunk.message.content):
                        if text:
                            yield (
                                ThinkingChunk(text)
                                if kind == "thinking"
                                else TextChunk(text)
                            )
                            yielded = True
                if chunk.message.tool_calls:
                    tool_calls = []
                    for i, tc in enumerate(chunk.message.tool_calls):
                        logger.debug(
                            "Ollama raw tool call: name=%s arguments=%s",
                            tc.function.name,
                            dict(tc.function.arguments),
                        )
                        tool_calls.append(
                            ToolCall(
                                id=f"call_{i}",
                                name=tc.function.name,
                                description="",
                                parameters=dict(tc.function.arguments),
                            )
                        )
                    yield ToolCallChunk(tool_calls)
                    yielded = True
            for kind, text in parser.flush():
                if text:
                    yield ThinkingChunk(text) if kind == "thinking" else TextChunk(text)
                    yielded = True
            if not yielded:
                logger.warning(
                    "Ollama stream completed with no content or tool calls (model=%s)",
                    self.model,
                )
        except Exception as e:
            logger.exception(
                "Error in %s.stream_generate: %s", self.__class__.__name__, str(e)
            )
            raise


__all__ = ["OllamaBackend"]
