from __future__ import annotations

import json
import logging
from typing import AsyncIterator, Dict, List, Optional

from huggingface_hub import AsyncInferenceClient, ChatCompletionOutput

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


class HuggingFaceBackend(ModelBackend):
    """HuggingFace Inference backend using the official huggingface_hub SDK.

    When ``remote`` is True the client connects to the HuggingFace Inference
    API (requires a valid ``api_key``).  When False (the default) it connects
    to a local inference server at ``base_url`` (e.g. TGI, vLLM).
    """

    def __init__(
        self,
        model: str,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        if not api_key and not base_url or not model:
            raise ValueError(
                "model or base_url, plus api_key are required when connecting to the remote HuggingFace Inference API"
            )
        self.model = model or base_url
        self.api_key = api_key
        self.base_url = base_url
        self.client = AsyncInferenceClient(base_url=self.model, api_key=api_key)

    def set_base_url(self, url: str) -> None:
        self.base_url = url
        self.client = AsyncInferenceClient(base_url=url, api_key=self.api_key)

    async def generate(
        self,
        model: Optional[str],
        messages: List[Message],
        tools: Optional[List[Dict]] = None,
        max_tokens: int = MAX_TOKENS,
        temperature: float = TEMPERATURE,
    ) -> ModelResponse:
        active_model = model or self.model
        logger.debug(
            "Generating response with %s: model=%s, messages=%s, tools=%s",
            self.__class__.__name__,
            active_model,
            messages,
            tools if tools else [],
        )
        try:
            response: ChatCompletionOutput = await self.client.chat.completions.create(
                model=active_model,
                messages=_format_messages(messages, stringify_arguments=True),
                tools=_format_tools(tools) if tools else [],
                tool_choice="auto" if tools else "none",
                max_tokens=max_tokens,
                temperature=temperature,
            )
            message = response.choices[0].message
            tool_calls = []
            if message.tool_calls:
                tool_calls = [
                    ToolCall(
                        id=tc.id or f"call_{i}",
                        name=tc.function.name,
                        description=tc.function.description or "",
                        parameters=json.loads(tc.function.arguments or "{}"),
                    )
                    for i, tc in enumerate(message.tool_calls)
                ]
            return ModelResponse(
                content=message.content or "",
                tool_calls=tool_calls or None,
                finish_reason=response.choices[0].finish_reason or "stop",
            )
        except Exception as e:
            logger.exception(
                "Error in %s.generate: %s", self.__class__.__name__, str(e)
            )
            return ModelResponse(content="", tool_calls=None, finish_reason="error")

    async def stream_generate(
        self,
        messages: List[Message],
        tools: Optional[List[Dict]] = None,
    ) -> AsyncIterator[StreamEvent]:
        try:
            stream = await self.client.chat.completions.create(
                model=self.model,
                messages=_format_messages(messages, stringify_arguments=True),
                tools=_format_tools(tools or []),
                tool_choice="auto" if tools else "none",
                stream=True,
            )
            tool_calls_acc: Dict[int, dict] = {}
            flushed = False
            parser = _StreamingThinkParser()
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta:
                    delta = chunk.choices[0].delta
                    reasoning = getattr(delta, "reasoning_content", None)
                    if reasoning:
                        yield ThinkingChunk(reasoning)
                    if delta.content:
                        for kind, text in parser.feed(delta.content):
                            if text:
                                yield (
                                    ThinkingChunk(text)
                                    if kind == "thinking"
                                    else TextChunk(text)
                                )
                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index
                            if idx not in tool_calls_acc:
                                tool_calls_acc[idx] = {
                                    "id": tc.id or f"call_{idx}",
                                    "name": "",
                                    "arguments": "",
                                }
                            if tc.function:
                                if tc.function.name:
                                    tool_calls_acc[idx]["name"] = tc.function.name
                                if tc.function.arguments:
                                    tool_calls_acc[idx][
                                        "arguments"
                                    ] += tc.function.arguments
                finish_reason = (
                    chunk.choices[0].finish_reason if chunk.choices else None
                )
                if (
                    finish_reason in ("tool_calls", "stop")
                    and tool_calls_acc
                    and not flushed
                ):
                    for data in tool_calls_acc.values():
                        logger.debug(
                            "HuggingFace raw tool call: id=%s name=%s arguments=%s",
                            data["id"],
                            data["name"],
                            data["arguments"],
                        )
                    tool_calls = [
                        ToolCall(
                            id=data["id"],
                            name=data["name"],
                            description="",
                            parameters=json.loads(data["arguments"] or "{}"),
                        )
                        for data in tool_calls_acc.values()
                    ]
                    yield ToolCallChunk(tool_calls)
                    flushed = True

            for kind, text in parser.flush():
                if text:
                    yield ThinkingChunk(text) if kind == "thinking" else TextChunk(text)

            if tool_calls_acc and not flushed:
                for data in tool_calls_acc.values():
                    logger.debug(
                        "HuggingFace raw tool call (fallback flush): id=%s name=%s arguments=%s",
                        data["id"],
                        data["name"],
                        data["arguments"],
                    )
                tool_calls = [
                    ToolCall(
                        id=data["id"],
                        name=data["name"],
                        description="",
                        parameters=json.loads(data["arguments"] or "{}"),
                    )
                    for data in tool_calls_acc.values()
                ]
                yield ToolCallChunk(tool_calls)
        except Exception as e:
            logger.exception(
                "Error in %s.stream_generate: %s", self.__class__.__name__, str(e)
            )
            raise


__all__ = ["HuggingFaceBackend"]
