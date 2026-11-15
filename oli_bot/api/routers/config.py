"""Config read/write routes under ``/v1/config`` (browser UI settings view)."""

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request

from ...settings import SETTINGS_FLAT_MAP, SettingsManager

router = APIRouter(prefix="/v1/config", tags=["config"])

# Mapping between the browser UI's flat ``OliConfig`` keys and the nested
# settings.json format the ``SettingsManager`` persists. Kept as a module
# alias so existing imports/tests continue to work.
_FLAT_TO_NESTED = SETTINGS_FLAT_MAP


def _flat_to_nested(flat: Dict[str, Any], settings: Dict[str, Any]) -> Dict[str, Any]:
    """Overlay a flat OliConfig dict (from the browser) onto nested settings."""
    for flat_key, (group, nested_key) in _FLAT_TO_NESTED.items():
        if flat_key not in flat:
            continue
        if group:
            settings.setdefault(group, {})[nested_key] = flat[flat_key]
        else:
            settings[nested_key] = flat[flat_key]
    return settings


def _nested_to_flat(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten nested settings into the browser's OliConfig shape."""
    flat: Dict[str, Any] = {}
    for flat_key, (group, nested_key) in _FLAT_TO_NESTED.items():
        if group:
            flat[flat_key] = settings.get(group, {}).get(nested_key)
        else:
            flat[flat_key] = settings.get(nested_key)
    return flat


@router.get("")
async def get_config() -> Dict[str, Any]:
    """Return the server's current configuration in flat OliConfig form."""
    settings = SettingsManager().load()
    return _nested_to_flat(settings)


@router.put("")
async def update_config(request: Request, flat: Dict[str, Any]) -> Any:
    """Persist an updated config to ``~/.config/oli/settings.json``.

    Only the OliConfig fields from the browser are overlaid onto the existing
    settings, so secrets/env-driven values untouched by the UI are preserved.
    The running agent is not rebuilt; restart the server for changes to take
    effect.

    ``api_profile`` is the exception: it is only persisted here, and swapping
    the live agent's profile needs ``PUT /v1/profiles/{name}``.
    """
    manager = SettingsManager()
    settings = _flat_to_nested(flat, manager.load())
    try:
        config = manager.to_appconfig(settings)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid config: {e}")
    manager.save(settings)
    request.app.state.config = config
    return _nested_to_flat(settings)
