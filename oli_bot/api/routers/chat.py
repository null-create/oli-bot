"""OpenAI-compatible chat routes: ``/v1/models`` and ``/v1/chat/completions``."""

import time
from typing import Any, Dict

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from ...agent import Agent
from ..deps import get_agent, get_lock
from ...models import ChatCompletionRequest
from ..runner import _collect_response, _stream_response, _completion_id, _usage

router = APIRouter(prefix="/v1", tags=["chat"])


@router.get("/models")
async def list_models(agent: Agent = Depends(get_agent)) -> Dict[str, Any]:
    model = str(agent.backend.model or "")
    return {
        "object": "list",
        "data": [
            {
                "id": f"oli-bot-{model}",
                "object": "model",
                "created": int(time.time()),
                "owned_by": "oli",
            }
        ],
    }


@router.post("/chat/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    agent: Agent = Depends(get_agent),
    lock: Any = Depends(get_lock),
) -> Any:
    if request.stream:
        return StreamingResponse(
            _stream_response(request, agent, lock),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    completion_text = await _collect_response(request, agent, lock)
    return {
        "id": _completion_id(),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": str(agent.backend.model or ""),
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": completion_text},
                "finish_reason": "stop",
            }
        ],
        "usage": _usage(completion_text),
    }
