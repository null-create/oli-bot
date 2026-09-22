"""OpenAI wire-message and agent-event conversion helpers."""

import base64
import dataclasses
import logging
from typing import TYPE_CHECKING, Any, Dict, List

from ..models import (
    AssistantResponse,
    Done,
    Error,
    ImageAttachment,
    Message,
    StreamChunk,
    SubAgentCompleted,
    SubAgentEvent,
    SubAgentProgress,
    SubAgentStarted,
    ThinkingChunk,
    ToolCallExecuting,
    ToolCallResult,
    UsageEvent,
    ChatCompletionMessage,
)

if TYPE_CHECKING:
    from ..agent import AgentEvent

logger = logging.getLogger(__name__)


def _media_type_from_data_uri(data_uri: str) -> str:
    header = data_uri.split(",", 1)[0]
    if ";" in header:
        header = header.split(";", 1)[0]
    if ":" in header:
        header = header.split(":", 1)[1]
    return header or "application/octet-stream"


def _decode_data_uri(data_uri: str) -> bytes:
    return base64.b64decode(data_uri.split(",", 1)[1])


def _to_message(msg: ChatCompletionMessage) -> Message:
    """Convert an OpenAI chat message into an internal ``Message``.

    ``content`` may be a plain string or a list of parts (for multimodal);
    ``image_url`` parts with ``data:`` URIs become ``ImageAttachment``
    instances carried through the tool loop for vision-capable backends.
    """
    role = msg.role
    content = msg.content
    images: List[ImageAttachment] = []

    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text_parts = []
        for part in content:
            if not isinstance(part, dict):
                continue
            ptype = part.get("type")
            if ptype == "text":
                text_parts.append(part.get("text", ""))
            elif ptype == "image_url":
                url = part.get("image_url")
                if isinstance(url, dict):
                    url = url.get("url", "")
                if isinstance(url, str) and url.startswith("data:"):
                    images.append(
                        ImageAttachment(
                            data=_decode_data_uri(url),
                            media_type=_media_type_from_data_uri(url),
                        )
                    )
                elif isinstance(url, str):
                    text_parts.append(f"[image: {url}]")
        text = "\n".join(text_parts)
    else:
        text = str(content)

    return Message(
        role=role,
        content=text,
        images=images or None,
        name=msg.name,
    )


def _event_to_frame(event: "AgentEvent") -> Dict[str, Any]:
    """Convert an ``AgentEvent`` into a typed JSON envelope for the browser.

    The ``type`` field lets the client distinguish event kinds and render them
    differently (streamed text, thinking blocks, tool calls, errors, etc.).
    """
    if isinstance(event, SubAgentEvent):
        # Wrapped sub-agent activity: forward the inner frame with the owning
        # run's identity attached so the client can demux by task_id.
        frame = _event_to_frame(event.event)
        frame["data"]["task_id"] = event.task_id
        frame["data"]["agent_name"] = event.agent_name
        return frame
    if isinstance(event, StreamChunk):
        return {"type": "text_chunk", "data": {"text": event.text}}
    if isinstance(event, ThinkingChunk):
        return {"type": "thinking", "data": {"text": event.text}}
    if isinstance(event, ToolCallExecuting):
        return {
            "type": "tool_call_executing",
            "data": {"name": event.name, "parameters": event.parameters},
        }
    if isinstance(event, ToolCallResult):
        return {
            "type": "tool_call_result",
            "data": {"name": event.name, "result": event.result},
        }
    if isinstance(event, AssistantResponse):
        return {"type": "assistant_response", "data": {"content": event.content}}
    if isinstance(event, UsageEvent):
        return {"type": "usage", "data": dataclasses.asdict(event.usage)}
    if isinstance(event, Error):
        return {"type": "error", "data": {"message": event.message}}
    if isinstance(event, Done):
        return {"type": "done", "data": {"full_text": event.full_text}}
    if isinstance(event, SubAgentStarted):
        return {
            "type": "sub_agent_started",
            "data": {
                "task_id": event.task_id,
                "agent_name": event.agent_name,
                "pool_name": event.pool_name,
                "task": event.task,
            },
        }
    if isinstance(event, SubAgentProgress):
        return {
            "type": "sub_agent_progress",
            "data": {
                "task_id": event.task_id,
                "agent_name": event.agent_name,
                "activity": event.activity,
                "status": event.status,
            },
        }
    if isinstance(event, SubAgentCompleted):
        return {
            "type": "sub_agent_completed",
            "data": {
                "task_id": event.task_id,
                "agent_name": event.agent_name,
                "status": event.status,
                "full_text": event.full_text,
            },
        }
    logger.warning("Unknown agent event in websocket relay: %r", event)
    return {"type": "unknown", "data": {"event": repr(event)}}
