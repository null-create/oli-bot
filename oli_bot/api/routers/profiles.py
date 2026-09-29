"""Agent profile listing and selection under ``/v1/profiles``."""

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Request

from ...agent import Agent
from ...settings import SettingsManager
from ..deps import get_agent, get_lock

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/profiles", tags=["profiles"])


def _profile_summary(agent: Agent, name: str) -> Dict[str, Any]:
    """Describe a single profile from its manifest.

    The active profile's already-resolved data is reused rather than
    re-loaded; for every other name the manager is asked to load it so a
    broken ``AGENTS.md`` surfaces as a skip rather than taking down the whole
    listing.
    """
    if name == agent.profile_name and agent.profile_data is not None:
        data = agent.profile_data
    else:
        data = agent.profile_manager.load_profile(name)
    manifest = data.manifest
    return {
        "name": name,
        "active": name == agent.profile_name,
        "description": manifest.description,
        "version": manifest.version,
        "default_model_tier": manifest.default_model_tier,
        "allow_tools": list(manifest.permissions.allow_tools),
        "deny_tools": list(manifest.permissions.deny_tools),
    }


@router.get("")
async def list_profiles(agent: Agent = Depends(get_agent)) -> List[Dict[str, Any]]:
    """List the profiles available on disk, flagging the active one."""
    summaries: List[Dict[str, Any]] = []
    for name in agent.list_profiles():
        try:
            summaries.append(_profile_summary(agent, name))
        except ValueError as e:
            # One unloadable profile must not break the menu.
            summaries.append(
                {
                    "name": name,
                    "active": name == agent.profile_name,
                    "description": f"unavailable: {e}",
                    "version": "",
                    "default_model_tier": "",
                    "allow_tools": [],
                    "deny_tools": [],
                }
            )
    return summaries


@router.put("/{name}")
async def select_profile(
    name: str,
    request: Request,
    agent: Agent = Depends(get_agent),
    lock: Any = Depends(get_lock),
) -> Dict[str, Any]:
    """Make ``name`` the active profile for the shared agent.

    The swap is serialized on the same process-wide lock used by the chat
    endpoints so a profile never changes mid-stream. The choice is persisted
    to ``settings.json`` so a restart resumes on the same profile.

    The agent is a process-global singleton, so this affects every connected
    client, not just the one that requested it.
    """
    async with lock:
        try:
            agent.load_profile(name)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))

    manager = SettingsManager()
    try:
        settings = manager.load()
        settings.setdefault("api_server", {})["profile"] = name
        manager.save(settings)
        request.app.state.config.api_profile = name
    except Exception as e:
        # The swap already took effect; a settings write failure should not
        # report the selection as failed.
        logger.warning("Loaded profile '%s' but failed to persist it: %s", name, e)

    return _profile_summary(agent, name)
