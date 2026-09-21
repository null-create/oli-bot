"""Config read/write routes under ``/v1/config`` (browser UI settings view)."""

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request

from ...settings import SettingsManager

router = APIRouter(prefix="/v1/config", tags=["config"])

# Mapping between the browser UI's flat ``OliConfig`` keys and the nested
# settings.json format the ``SettingsManager`` persists.
_FLAT_TO_NESTED = {
    "backend": ("", "backend"),
    "openai_api_key": ("openai", "api_key"),
    "openai_base_url": ("openai", "base_url"),
    "openai_model": ("openai", "large_model"),
    "openai_small_model": ("openai", "small_model"),
    "openai_vision_style": ("openai", "vision_style"),
    "openai_optional_headers": ("openai", "optional_headers"),
    "ollama_base_url": ("ollama", "base_url"),
    "ollama_model": ("ollama", "large_model"),
    "ollama_small_model": ("ollama", "small_model"),
    "huggingface_base_url": ("huggingface", "base_url"),
    "huggingface_api_key": ("huggingface", "api_key"),
    "huggingface_model": ("huggingface", "large_model"),
    "huggingface_small_model": ("huggingface", "small_model"),
    "huggingface_remote": ("huggingface", "remote"),
    "transformers_model": ("transformers", "model"),
    "transformers_small_model": ("transformers", "small_model"),
    "transformers_device": ("transformers", "device"),
    "transformers_dtype": ("transformers", "dtype"),
    "transformers_is_multi_model": ("transformers", "is_multi_model"),
    "voice_whisper_model": ("voice", "whisper_model"),
    "voice_piper_model": ("voice", "piper_model"),
    "voice_sample_rate": ("voice", "sample_rate"),
    "voice_frame_duration_ms": ("voice", "frame_duration_ms"),
    "voice_vad_aggressiveness": ("voice", "vad_aggressiveness"),
    "voice_silence_timeout_ms": ("voice", "silence_timeout_ms"),
    "voice_max_record_seconds": ("voice", "max_record_seconds"),
    "max_tokens": ("model_params", "max_tokens"),
    "temperature": ("model_params", "temperature"),
    "max_retries": ("model_params", "max_retries"),
    "retry_delay": ("model_params", "retry_delay"),
    "request_timeout": ("model_params", "request_timeout"),
    "max_messages": ("model_params", "max_messages"),
    "max_tool_iterations": ("model_params", "max_tool_iterations"),
    "stream_timeout": ("model_params", "stream_timeout"),
    "model_filters": ("model_params", "model_filters"),
    "truncation_max_chars_small": ("model_params", "truncation_max_chars_small"),
    "truncation_max_chars_large": ("model_params", "truncation_max_chars_large"),
    "dry_run": ("model_params", "dry_run"),
    "offline_mode": ("model_params", "offline_mode"),
    "use_agent_pool": ("model_params", "use_agent_pool"),
    "agent_pool_size": ("model_params", "agent_pool_size"),
    "agents_yaml": ("model_params", "agents_yaml"),
    "log_level": ("logging", "log_level"),
    "log_file": ("logging", "log_file"),
    "profiles_dir": ("paths", "profiles_dir"),
    "logs_dir": ("paths", "logs_dir"),
    "api_host": ("api_server", "host"),
    "api_port": ("api_server", "port"),
    "api_profile": ("api_server", "profile"),
    "api_mode": ("api_server", "mode"),
}


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