"""Settings management — load/save/merge settings.json with env var fallback."""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from .config import AppConfig

logger = logging.getLogger(__name__)

DEFAULT_SETTINGS: Dict[str, Any] = {
    "backend": "ollama",
    "openai": {
        "api_key": "",
        "base_url": "https://api.openai.com/v1",
        "large_model": "gpt-5",
        "small_model": "gpt-5-mini",
        "vision_style": "openai",
    },
    "ollama": {
        "base_url": "http://localhost:11434",
        "large_model": "",
        "small_model": "",
    },
    "huggingface": {
        "base_url": "https://api-inference.huggingface.co",
        "api_key": "",
        "large_model": "",
        "small_model": "",
        "remote": False,
    },
    "transformers": {
        "model": "",
        "small_model": "",
        "device": "auto",
        "dtype": "auto",
        "is_multi_model": False,
    },
    "model_params": {
        "max_tokens": 2048,
        "temperature": 0.7,
        "max_retries": 3,
        "retry_delay": 1.0,
        "request_timeout": 30.0,
        "max_messages": 100,
        "max_tool_iterations": 25,
        "stream_timeout": 240.0,
        "model_filters": "",
        "truncation_max_chars_small": 4000,
        "truncation_max_chars_large": 100000,
        "dry_run": False,
        "offline_mode": True,
        "use_agent_pool": False,
        "agent_pool_size": 5,
    },
    "logging": {
        "log_level": "INFO",
        "log_file": "logs/backend.ndjson",
    },
    "api_server": {
        "host": "0.0.0.0",
        "port": 8000,
        "profile": "default",
        "mode": "agent",
    },
    "paths": {
        "profiles_dir": "profiles",
        "logs_dir": "logs",
    },
    "workspace": {
        "max_workspaces": 20,
    },
    "session": {
        "auto_save": True,
        "resume_prompt": True,
    },
}

# Mapping: env var -> dot-separated path into settings dict
ENV_TO_SETTINGS: dict[str, str] = {
    "OLI_BACKEND": "backend",
    "OLI_OPENAI_API_KEY": "openai.api_key",
    "OLI_OPENAI_BASE_URL": "openai.base_url",
    "OLI_OPENAI_MODEL": "openai.large_model",
    "OLI_OPENAI_SMALL_MODEL": "openai.small_model",
    "OLI_OPENAI_VISION_STYLE": "openai.vision_style",
    "OLI_OLLAMA_BASE_URL": "ollama.base_url",
    "OLI_OLLAMA_MODEL": "ollama.large_model",
    "OLI_OLLAMA_SMALL_MODEL": "ollama.small_model",
    "OLI_HUGGINGFACE_BASE_URL": "huggingface.base_url",
    "OLI_HUGGINGFACE_API_KEY": "huggingface.api_key",
    "OLI_HUGGINGFACE_MODEL": "huggingface.large_model",
    "OLI_HUGGINGFACE_SMALL_MODEL": "huggingface.small_model",
    "OLI_HUGGINGFACE_REMOTE": "huggingface.remote",
    "OLI_TRANSFORMERS_MODEL": "transformers.model",
    "OLI_TRANSFORMERS_SMALL_MODEL": "transformers.small_model",
    "OLI_TRANSFORMERS_DEVICE": "transformers.device",
    "OLI_TRANSFORMERS_DTYPE": "transformers.dtype",
    "OLI_TRANSFORMERS_IS_MULTI_MODEL": "transformers.is_multi_model",
    "OLI_MAX_TOKENS": "model_params.max_tokens",
    "OLI_TEMPERATURE": "model_params.temperature",
    "OLI_MAX_RETRIES": "model_params.max_retries",
    "OLI_RETRY_DELAY": "model_params.retry_delay",
    "OLI_REQUEST_TIMEOUT": "model_params.request_timeout",
    "OLI_MAX_MESSAGES": "model_params.max_messages",
    "OLI_MAX_TOOL_ITERATIONS": "model_params.max_tool_iterations",
    "OLI_STREAM_TIMEOUT": "model_params.stream_timeout",
    "OLI_MODEL_FILTERS": "model_params.model_filters",
    "OLI_TRUNCATION_SMALL": "model_params.truncation_max_chars_small",
    "OLI_TRUNCATION_LARGE": "model_params.truncation_max_chars_large",
    "OLI_DRY_RUN": "model_params.dry_run",
    "OLI_OFFLINE_MODE": "model_params.offline_mode",
    "OLI_USE_AGENT_POOL": "model_params.use_agent_pool",
    "OLI_AGENT_POOL_SIZE": "model_params.agent_pool_size",
    "OLI_LOG_LEVEL": "logging.log_level",
    "OLI_LOG_FILE": "logging.log_file",
    "OLI_API_HOST": "api_server.host",
    "OLI_API_PORT": "api_server.port",
    "OLI_API_PROFILE": "api_server.profile",
    "OLI_API_MODE": "api_server.mode",
    "OLI_PROFILES_DIR": "paths.profiles_dir",
    "OLI_LOGS_DIR": "paths.logs_dir",
}

# SDK-standard env names accepted as fallbacks when the OLI_-prefixed
# variant is unset. Applied BEFORE ENV_TO_SETTINGS so the OLI_ prefix
# still wins when both are set.
ENV_FALLBACKS: dict[str, str] = {
    "OPENAI_API_KEY": "openai.api_key",
    "OPENAI_BASE_URL": "openai.base_url",
    "HUGGINGFACE_API_KEY": "huggingface.api_key",
    "HF_TOKEN": "huggingface.api_key",
}


def _deep_set(d: dict, path: str, value: Any) -> None:
    """Set a value in a nested dict using a dot-separated path."""
    parts = path.split(".")
    for part in parts[:-1]:
        d = d.setdefault(part, {})
    d[parts[-1]] = value


def _deep_get(d: dict, path: str) -> Any:
    """Get a value from a nested dict using a dot-separated path."""
    parts = path.split(".")
    for part in parts:
        if isinstance(d, dict):
            d = d.get(part)
        else:
            return None
    return d


def _deep_merge(base: dict, overlay: dict) -> dict:
    """Deep-merge overlay into base (overlay wins)."""
    result = {}
    for key in base:
        if key in overlay:
            if isinstance(base[key], dict) and isinstance(overlay[key], dict):
                result[key] = _deep_merge(base[key], overlay[key])
            else:
                result[key] = overlay[key]
        else:
            result[key] = base[key]
    for key in overlay:
        if key not in result:
            result[key] = overlay[key]
    return result


class SettingsManager:
    """Load, merge, persist application settings."""

    def __init__(self, config_dir: Optional[Path] = None):
        if config_dir is None:
            config_dir = Path.home() / ".config" / "oli"
        self._config_dir = config_dir
        self._config_dir.mkdir(parents=True, exist_ok=True)
        self._settings_path = self._config_dir / "settings.json"

    @property
    def settings_path(self) -> Path:
        return self._settings_path

    def get_defaults(self) -> Dict[str, Any]:
        return _deep_merge({}, DEFAULT_SETTINGS)

    def _load_file(self) -> Dict[str, Any]:
        if not self._settings_path.exists():
            return {}
        try:
            return json.loads(self._settings_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load settings.json: %s", e)
            return {}

    def _ensure_settings_file(self, source: Optional[AppConfig] = None) -> None:
        if self._settings_path.exists():
            return
        self.save(self.from_appconfig(source or AppConfig()))
        logger.info(
            "Created settings.json at %s from env/.env + defaults", self._settings_path
        )

    def _apply_env_overrides(self, settings: dict) -> dict:
        # Fallbacks first so OLI_-prefixed vars still win when both are set.
        for env_var, path in ENV_FALLBACKS.items():
            value = os.environ.get(env_var)
            if value is not None:
                _deep_set(
                    settings, path, _coerce(value, _deep_get(DEFAULT_SETTINGS, path))
                )
        for env_var, path in ENV_TO_SETTINGS.items():
            value = os.environ.get(env_var)
            if value is not None:
                _deep_set(
                    settings, path, _coerce(value, _deep_get(DEFAULT_SETTINGS, path))
                )
        return settings

    def load(self) -> Dict[str, Any]:
        self._ensure_settings_file()
        settings = self.get_defaults()
        settings = self._apply_env_overrides(settings)
        file_settings = self._load_file()
        settings = _deep_merge(settings, file_settings)
        return settings

    def save(self, settings: dict) -> None:
        try:
            self._settings_path.write_text(
                json.dumps(settings, indent=2), encoding="utf-8"
            )
            logger.info("Settings saved to %s", self._settings_path)
        except OSError as e:
            logger.error("Failed to save settings: %s", e)

    def to_appconfig(self, settings: dict) -> AppConfig:
        """Convert nested settings dict to a flat AppConfig.

        The ``settings`` dict passed in is already env-resolved via
        :meth:`load`, so we pass every value as an explicit kwarg to
        preserve the documented precedence: JSON file > env vars >
        declared defaults.

        Secrets (API keys) are a special case: an empty string in
        ``settings.json`` is almost never intentional, so we fall back
        to the SDK-standard env names (``OPENAI_API_KEY``,
        ``HUGGINGFACE_API_KEY`` / ``HF_TOKEN``) when the resolved value
        is empty. This prevents ``/config`` writing a blank JSON entry
        from silently overriding a real ``.env`` secret.
        """
        op = settings.get("openai", {})
        ol = settings.get("ollama", {})
        hf = settings.get("huggingface", {})
        tr = settings.get("transformers", {})
        mp = settings.get("model_params", {})
        lg = settings.get("logging", {})
        api = settings.get("api_server", {})
        paths = settings.get("paths", {})
        openai_key = (
            op.get("api_key", "")
            or os.environ.get("OLI_OPENAI_API_KEY", "")
            or os.environ.get("OPENAI_API_KEY", "")
        )
        hf_key = (
            hf.get("api_key", "")
            or os.environ.get("OLI_HUGGINGFACE_API_KEY", "")
            or os.environ.get("HUGGINGFACE_API_KEY", "")
            or os.environ.get("HF_TOKEN", "")
        )
        hf_remote_raw = hf.get("remote")
        if hf_remote_raw is None:
            hf_remote_env = os.environ.get("OLI_HUGGINGFACE_REMOTE")
            hf_remote = (
                hf_remote_env.lower() in ("1", "true", "yes")
                if hf_remote_env is not None
                else False
            )
        else:
            hf_remote = bool(hf_remote_raw)
        return AppConfig(
            backend=settings.get("backend", "ollama"),
            openai_api_key=openai_key,
            openai_base_url=op.get("base_url", "https://api.openai.com/v1"),
            openai_model=op.get("large_model", "gpt-5"),
            openai_small_model=op.get("small_model", "gpt-5-mini"),
            openai_vision_style=op.get("vision_style", "openai"),
            ollama_base_url=ol.get("base_url", "http://localhost:11434"),
            ollama_model=ol.get("large_model", ""),
            ollama_small_model=ol.get("small_model", ""),
            huggingface_base_url=hf.get(
                "base_url", "https://api-inference.huggingface.co"
            ),
            huggingface_api_key=hf_key,
            huggingface_model=hf.get("large_model", ""),
            huggingface_small_model=hf.get("small_model", ""),
            huggingface_remote=hf_remote,
            transformers_model=tr.get("model", ""),
            transformers_small_model=tr.get("small_model", ""),
            transformers_device=tr.get("device", "auto"),
            transformers_dtype=tr.get("dtype", "auto"),
            transformers_is_multi_model=tr.get("is_multi_model", False),
            max_tokens=mp.get("max_tokens", 2048),
            temperature=mp.get("temperature", 0.7),
            max_retries=mp.get("max_retries", 3),
            retry_delay=mp.get("retry_delay", 1.0),
            request_timeout=mp.get("request_timeout", 30.0),
            max_messages=mp.get("max_messages", 100),
            max_tool_iterations=mp.get("max_tool_iterations", 25),
            stream_timeout=mp.get("stream_timeout", 240.0),
            model_filters=mp.get("model_filters", ""),
            truncation_max_chars_small=mp.get("truncation_max_chars_small", 4000),
            truncation_max_chars_large=mp.get("truncation_max_chars_large", 100000),
            dry_run=mp.get("dry_run", False),
            offline_mode=mp.get("offline_mode", True),
            use_agent_pool=mp.get("use_agent_pool", False),
            agent_pool_size=mp.get("agent_pool_size", 5),
            log_level=lg.get("log_level", "INFO"),
            log_file=lg.get("log_file", "logs/backend.ndjson"),
            api_host=api.get("host", "0.0.0.0"),
            api_port=api.get("port", 8000),
            api_profile=api.get("profile", "default"),
            api_mode=api.get("mode", "agent"),
            profiles_dir=paths.get("profiles_dir", "profiles"),
            logs_dir=paths.get("logs_dir", "logs"),
        )

    def from_appconfig(self, config: AppConfig) -> dict:
        """Convert AppConfig back to nested settings dict."""
        settings = self.get_defaults()
        settings["backend"] = config.backend
        settings["openai"]["api_key"] = config.openai_api_key
        settings["openai"]["base_url"] = config.openai_base_url
        settings["openai"]["large_model"] = config.openai_model
        settings["openai"]["small_model"] = config.openai_small_model
        settings["openai"]["vision_style"] = config.openai_vision_style
        settings["ollama"]["base_url"] = config.ollama_base_url
        settings["ollama"]["large_model"] = config.ollama_model
        settings["ollama"]["small_model"] = config.ollama_small_model
        settings["huggingface"]["base_url"] = config.huggingface_base_url
        settings["huggingface"]["api_key"] = config.huggingface_api_key
        settings["huggingface"]["large_model"] = config.huggingface_model
        settings["huggingface"]["small_model"] = config.huggingface_small_model
        settings["huggingface"]["remote"] = config.huggingface_remote
        settings["transformers"]["model"] = config.transformers_model
        settings["transformers"]["small_model"] = config.transformers_small_model
        settings["transformers"]["device"] = config.transformers_device
        settings["transformers"]["dtype"] = config.transformers_dtype
        settings["transformers"]["is_multi_model"] = config.transformers_is_multi_model
        settings["model_params"]["use_agent_pool"] = config.use_agent_pool
        settings["model_params"]["agent_pool_size"] = config.agent_pool_size
        settings["model_params"]["max_tokens"] = config.max_tokens
        settings["model_params"]["temperature"] = config.temperature
        settings["model_params"]["max_retries"] = config.max_retries
        settings["model_params"]["retry_delay"] = config.retry_delay
        settings["model_params"]["request_timeout"] = config.request_timeout
        settings["model_params"]["max_messages"] = config.max_messages
        settings["model_params"]["max_tool_iterations"] = config.max_tool_iterations
        settings["model_params"]["stream_timeout"] = config.stream_timeout
        settings["model_params"]["model_filters"] = config.model_filters
        settings["model_params"][
            "truncation_max_chars_small"
        ] = config.truncation_max_chars_small
        settings["model_params"][
            "truncation_max_chars_large"
        ] = config.truncation_max_chars_large
        settings["model_params"]["dry_run"] = config.dry_run
        settings["model_params"]["offline_mode"] = config.offline_mode
        settings["logging"]["log_level"] = config.log_level
        settings["logging"]["log_file"] = config.log_file
        settings["api_server"]["host"] = config.api_host
        settings["api_server"]["port"] = config.api_port
        settings["api_server"]["profile"] = config.api_profile
        settings["api_server"]["mode"] = config.api_mode
        settings["paths"]["profiles_dir"] = config.profiles_dir
        settings["paths"]["logs_dir"] = config.logs_dir
        return settings


def _coerce(value: str, type_hint: Any) -> Any:
    """Coerce a string env var to match the type of the default value."""
    if isinstance(type_hint, bool):
        return value.lower() in ("1", "true", "yes")
    if isinstance(type_hint, int):
        return int(value)
    if isinstance(type_hint, float):
        return float(value)
    return value
