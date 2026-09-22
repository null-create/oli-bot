"""Workspace and filesystem routes: ``/v1/workspace`` and ``/v1/fs/list``."""

from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException

from ...agent import Agent
from ..deps import get_agent, get_workspace_manager
from ..schemas import SetWorkspaceBody
from ...sessions import SCOPE_WORKSPACE_SENSITIVE, WorkspaceManager, is_sensitive_path

router = APIRouter(tags=["workspace"])

_FS_LIST_LIMIT = 500


def _workspace_state(agent: Agent, manager: WorkspaceManager) -> Dict[str, Any]:
    """Snapshot the active workspace plus the recently used list."""
    current = getattr(agent._session, "workspace", None)
    current_str = str(current) if current else None
    return {
        "current": current_str,
        "sensitive": bool(current and is_sensitive_path(current)),
        "workspaces": [str(w) for w in manager.list_workspaces()],
    }


def _list_dir(path: Path) -> List[Dict[str, Any]]:
    """List one directory as JSON-safe entries (dirs first, alphabetized)."""
    entries: List[Dict[str, Any]] = []
    for child in path.iterdir():
        try:
            child_type = "dir" if child.is_dir() else "file"
        except OSError:
            continue
        entries.append(
            {
                "name": child.name,
                "path": str(child.resolve()),
                "type": child_type,
                "sensitive": is_sensitive_path(child) if child_type == "dir" else False,
            }
        )
    entries.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))
    return entries[:_FS_LIST_LIMIT]


@router.get("/v1/workspace")
async def get_workspace(
    agent: Agent = Depends(get_agent),
    manager: WorkspaceManager = Depends(get_workspace_manager),
) -> Dict[str, Any]:
    """Return the active workspace and the recently used workspace list."""
    return _workspace_state(agent, manager)


@router.put("/v1/workspace")
async def set_workspace(
    payload: SetWorkspaceBody,
    agent: Agent = Depends(get_agent),
    manager: WorkspaceManager = Depends(get_workspace_manager),
) -> Any:
    """Set the shared agent's workspace to an existing directory.

    Mirrors the TUI's ``/workspace set`` (without interactive prompts — the
    browser already required confirmation for sensitive paths): the session
    grants are cleared and, for a sensitive path, ``workspace_sensitive`` is
    re-granted so read scoping is respected.  The path is recorded in the
    recently-used list.
    """
    path_str = str(payload.path or "").strip()
    if not path_str:
        raise HTTPException(status_code=422, detail="Path is required")
    try:
        path = Path(path_str).expanduser().resolve()
    except (OSError, RuntimeError):
        raise HTTPException(status_code=422, detail=f"Invalid path: {path_str}")
    if not path.is_dir():
        raise HTTPException(status_code=422, detail=f"Not a valid directory: {path}")
    session = agent._session
    session.workspace = path
    session._session_grants.clear()
    if is_sensitive_path(path):
        session._session_grants.add(SCOPE_WORKSPACE_SENSITIVE)
    manager.add_workspace(path)
    return _workspace_state(agent, manager)


@router.delete("/v1/workspace")
async def unset_workspace(
    agent: Agent = Depends(get_agent),
    manager: WorkspaceManager = Depends(get_workspace_manager),
) -> Any:
    """Clear the active workspace (mirrors the TUI's ``/workspace unset``)."""
    session = agent._session
    session.workspace = None
    session._session_grants.clear()
    return _workspace_state(agent, manager)


@router.get("/v1/fs/list")
async def list_fs_directory(path: str = "/") -> Any:
    """List a directory on the server for the workspace browser."""
    try:
        resolved = Path(path).expanduser().resolve()
    except (OSError, RuntimeError):
        raise HTTPException(status_code=422, detail=f"Invalid path: {path}")
    if not resolved.is_dir():
        raise HTTPException(
            status_code=404, detail=f"Not a valid directory: {resolved}"
        )
    try:
        entries = _list_dir(resolved)
    except PermissionError:
        raise HTTPException(status_code=403, detail=f"Permission denied: {resolved}")
    return {
        "path": str(resolved),
        "sensitive": is_sensitive_path(resolved),
        "entries": entries,
    }
