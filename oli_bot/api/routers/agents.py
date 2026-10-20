"""Agent pool configuration listing under ``/v1/agents``."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Request

from ..deps import get_agent_pool
from ...agent import AgentPool

router = APIRouter(prefix="/v1/agents", tags=["agents"])


def _get_backend_type(backend: Any) -> str:
    """Return the backend type string for the given backend instance."""
    cls_name = type(backend).__name__
    mapping = {
        "OpenAIBackend": "openai",
        "OllamaBackend": "ollama",
        "HuggingFaceBackend": "huggingface",
        "TransformersBackend": "transformers",
    }
    return mapping.get(cls_name, cls_name.lower().replace("backend", ""))


def _mask_api_key(api_key: Optional[str]) -> str:
    """Mask API key for display purposes."""
    if not api_key:
        return "unset"
    return "set"


def _agent_to_dict(agent: Any) -> Dict[str, Any]:
    """Convert an Agent instance to a dict of effective config."""
    backend = getattr(agent, "backend", None)
    backend_dict: Dict[str, Any] = {}
    if backend is not None:
        backend_dict = {
            "type": _get_backend_type(backend),
            "model": getattr(backend, "model", None),
            "base_url": getattr(backend, "base_url", None),
            "api_key": _mask_api_key(getattr(backend, "api_key", None)),
        }

    return {
        "name": getattr(agent, "role", None) or getattr(agent, "name", ""),
        "profile": getattr(agent, "profile_name", "default"),
        "backend": backend_dict,
    }


@router.get("")
async def list_agents(
    request: Request,
    agent_pool: Optional[AgentPool] = Depends(get_agent_pool),
) -> Dict[str, Any]:
    """Return the agent pool configuration."""
    from ...agent import AgentPool as _AgentPool

    source_path: Optional[str] = None
    if agent_pool is not None and hasattr(agent_pool, "_resolve_agents_config_path"):
        try:
            source_path = agent_pool._resolve_agents_config_path()  # type: ignore[attr-defined]
        except Exception:
            source_path = None

    pools: List[Dict[str, Any]] = []
    enabled = agent_pool is not None

    if enabled and isinstance(agent_pool, _AgentPool):
        agent_pool_dict = getattr(agent_pool, "agent_pool", {})
        for pool_name, agents_dict in agent_pool_dict.items():
            agents_list = []
            for agent_name, agent in agents_dict.items():
                agent_data = _agent_to_dict(agent)
                if not agent_data["name"]:
                    agent_data["name"] = agent_name
                agents_list.append(agent_data)
            pools.append({"name": pool_name, "agents": agents_list})

    return {
        "enabled": enabled,
        "source": source_path,
        "pools": pools,
    }
