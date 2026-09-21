"""MCP server configuration CRUD under ``/v1/mcp``."""

import dataclasses
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from ...agent import Agent
from ..deps import get_agent
from ...models import MCPServerConfig

router = APIRouter(prefix="/v1/mcp", tags=["mcp"])


def _mcp_list(agent: Agent) -> List[Dict[str, Any]]:
    """Snapshot the current MCP server configs as a JSON-safe list."""
    return [
        dataclasses.asdict(cfg) for cfg in agent.mcp_manager.list_servers()
    ]


def _validate_mcp_config(cfg: MCPServerConfig) -> Optional[str]:
    """Return an error message for an invalid MCP server config, else None."""
    if not cfg.name or not cfg.name.strip():
        return "Server name is required"
    if cfg.transport not in ("stdio", "http"):
        return f"Unknown transport: {cfg.transport}"
    if cfg.transport == "http":
        if not cfg.url or not cfg.url.strip():
            return "URL is required for HTTP transport"
    elif not cfg.command or not cfg.command.strip():
        return "Command is required for stdio transport"
    return None


@router.get("")
async def list_mcp_servers(
    agent: Agent = Depends(get_agent),
) -> List[Dict[str, Any]]:
    """Return the configured MCP servers (from mcp_servers.json)."""
    return _mcp_list(agent)


@router.post("")
async def add_mcp_server(
    cfg: MCPServerConfig,
    agent: Agent = Depends(get_agent),
) -> Any:
    """Register a new MCP server and persist it to disk."""
    error = _validate_mcp_config(cfg)
    if error:
        raise HTTPException(status_code=422, detail=error)
    try:
        agent.mcp_manager.add_server(
            name=cfg.name,
            command=cfg.command,
            args=cfg.args,
            env=cfg.env,
            transport=cfg.transport,
            url=cfg.url,
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return _mcp_list(agent)


@router.put("/{name}")
async def update_mcp_server(
    name: str,
    cfg: MCPServerConfig,
    agent: Agent = Depends(get_agent),
) -> Any:
    """Update an existing MCP server (matches by path name) and persist."""
    error = _validate_mcp_config(cfg)
    if error:
        raise HTTPException(status_code=422, detail=error)
    try:
        agent.mcp_manager.update_server(
            name=name,
            command=cfg.command,
            args=cfg.args,
            env=cfg.env,
            transport=cfg.transport,
            url=cfg.url,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return _mcp_list(agent)


@router.delete("/{name}")
async def remove_mcp_server(
    name: str,
    agent: Agent = Depends(get_agent),
) -> Any:
    """Remove a configured MCP server and persist to disk."""
    try:
        agent.mcp_manager.remove_server(name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return _mcp_list(agent)