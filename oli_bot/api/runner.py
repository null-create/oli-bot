"""Agent execution for the REST chat endpoints."""

import json
import logging
import time
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional

from ..agent import Agent
from ..models import (
    AssistantResponse,
    ChatCompletionRequest,
    Done,
    Error,
    StreamChunk,
)
from .convert import _to_message
from .errors import AgentError
from .harness import _api_confirm

logger = logging.getLogger(__name__)


async def _resolve_tools(agent: Agent) -> Optional[List[Dict[str, Any]]]:
    mode = agent.mode
    if mode == "agent":
        return await agent.mcp_manager.get_available_tools()
    if mode == "ask":
        return await agent.mcp_manager.get_readonly_tools()
    if mode == "plan":
        return await agent.mcp_manager.get_plan_tools()
    return None


def _completion_id() -> str:
    return f"chatcmpl-{uuid.uuid4().hex}"


def _usage(completion_text: str) -> Dict[str, int]:
    # Rustic token estimate; real prompt usage depends on the backend. Kept
    # approximate since the agent loop does not surface exact counts.
    completion = max(1, (len(completion_text) + 3) // 4)
    return {
        "prompt_tokens": 0,
        "completion_tokens": completion,
        "total_tokens": completion,
    }


async def _collect_response(
    request: ChatCompletionRequest, agent: Agent, lock: Any
) -> str:
    """Run the agent tool loop for a request and return the final assistant
    text. Raises ``AgentError`` (formatted as an OpenAI error body) when the
    run fails or produces no text."""
    async with lock:
        messages = [_to_message(m) for m in request.messages]
        try:
            tools = await _resolve_tools(agent)
        except Exception as e:
            logger.warning("Failed to list tools: %s", e)
            tools = None

        full_text = ""
        error_text: Optional[str] = None
        try:
            async for event in agent.process(
                messages, tools=tools, confirm_callback=_api_confirm
            ):
                if isinstance(event, StreamChunk):
                    full_text += event.text
                elif isinstance(event, AssistantResponse):
                    full_text += event.content
                elif isinstance(event, Error):
                    error_text = event.message
                elif isinstance(event, Done):
                    if event.full_text:
                        full_text = event.full_text
        except Exception as e:
            logger.exception("Agent process failed: %s", e)
            error_text = str(e)

    if error_text:
        raise AgentError(error_text)
    if not full_text:
        logger.warning("Agent produced empty response for request")
        raise AgentError("The agent produced no response.", code="empty_response")
    return full_text


async def _stream_response(
    request: ChatCompletionRequest, agent: Agent, lock: Any
) -> AsyncIterator[str]:
    completion_id = _completion_id()
    model = str(agent.backend.model or "")
    created = int(time.time())

    def chunk(delta: Dict[str, Any], finish_reason: Any = None) -> str:
        return (
            "data: "
            + json.dumps(
                {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [
                        {"index": 0, "delta": delta, "finish_reason": finish_reason}
                    ],
                },
                ensure_ascii=False,
            )
            + "\n\n"
        )

    yield chunk({"role": "assistant", "content": ""})

    error_text: Optional[str] = None
    async with lock:
        messages = [_to_message(m) for m in request.messages]
        try:
            tools = await _resolve_tools(agent)
        except Exception as e:
            logger.warning("Failed to list tools: %s", e)
            tools = None
        try:
            async for event in agent.process(
                messages, tools=tools, confirm_callback=_api_confirm
            ):
                if isinstance(event, StreamChunk):
                    yield chunk({"content": event.text})
                elif isinstance(event, AssistantResponse):
                    yield chunk({"content": event.content})
                elif isinstance(event, Error):
                    error_text = event.message
        except Exception as e:
            logger.exception("Agent process failed during stream: %s", e)
            error_text = str(e)

    if error_text:
        error = {"message": error_text, "type": "server_error", "code": "agent_error"}
        yield "data: " + json.dumps({"error": error}) + "\n\n"
    else:
        yield chunk({}, finish_reason="stop")
    yield "data: [DONE]\n\n"
