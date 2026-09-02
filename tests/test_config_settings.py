"""Regression tests for AppConfig / SettingsManager precedence."""

import json

from oli_bot.config import AppConfig
from oli_bot.settings import SettingsManager


def test_appconfig_reads_env_var(monkeypatch):
    monkeypatch.setenv("OLI_MAX_TOKENS", "1234")
    monkeypatch.setenv("OLI_TEMPERATURE", "0.42")
    c = AppConfig()
    assert c.max_tokens == 1234
    assert c.temperature == 0.42


def test_appconfig_truncation_env_aliases(monkeypatch):
    # Documented public env vars — must keep working after the migration
    monkeypatch.setenv("OLI_TRUNCATION_SMALL", "999")
    monkeypatch.setenv("OLI_TRUNCATION_LARGE", "111222")
    c = AppConfig()
    assert c.truncation_max_chars_small == 999
    assert c.truncation_max_chars_large == 111222


def test_appconfig_falls_back_to_defaults():
    # _env_file=None bypasses .env loading so we assert pristine defaults
    # even when the repo has a populated .env for local development.
    c = AppConfig(_env_file=None)
    assert c.backend == "ollama"
    assert c.max_tool_iterations == 25
    assert c.offline_mode is True


def test_settings_manager_defaults_have_25_iterations(tmp_path):
    mgr = SettingsManager(config_dir=tmp_path)
    defaults = mgr.get_defaults()
    assert defaults["model_params"]["max_tool_iterations"] == 25


def test_settings_manager_file_overrides_env(tmp_path, monkeypatch):
    monkeypatch.setenv("OLI_MAX_TOKENS", "9999")
    (tmp_path / "settings.json").write_text('{"model_params": {"max_tokens": 2048}}')
    mgr = SettingsManager(config_dir=tmp_path)
    settings = mgr.load()
    cfg = mgr.to_appconfig(settings)
    # File > env, so max_tokens should be 2048 (from JSON), not 9999 (env)
    assert cfg.max_tokens == 2048


def test_settings_manager_env_overrides_defaults_when_no_file(tmp_path, monkeypatch):
    monkeypatch.setenv("OLI_MAX_TOKENS", "1500")
    mgr = SettingsManager(config_dir=tmp_path)
    settings = mgr.load()
    cfg = mgr.to_appconfig(settings)
    assert cfg.max_tokens == 1500


def test_settings_manager_creates_file_when_missing(tmp_path):
    mgr = SettingsManager(config_dir=tmp_path)
    assert not mgr.settings_path.exists()
    mgr.load()
    assert mgr.settings_path.exists()


def test_settings_manager_created_file_reflects_env(tmp_path, monkeypatch):
    monkeypatch.setenv("OLI_MAX_TOKENS", "1500")
    mgr = SettingsManager(config_dir=tmp_path)
    mgr.load()
    assert (
        json.loads(mgr.settings_path.read_text(encoding="utf-8"))["model_params"][
            "max_tokens"
        ]
        == 1500
    )


def test_settings_manager_created_file_uses_source_defaults(tmp_path):
    mgr = SettingsManager(config_dir=tmp_path)
    source = AppConfig(
        _env_file=None,
        backend="openai",
        openai_model="gpt-test",
        max_tokens=777,
    )
    mgr._ensure_settings_file(source=source)
    saved = json.loads(mgr.settings_path.read_text(encoding="utf-8"))
    assert saved["backend"] == "openai"
    assert saved["openai"]["large_model"] == "gpt-test"
    assert saved["model_params"]["max_tokens"] == 777
    assert saved["openai"]["base_url"] == "https://api.openai.com/v1"


def test_settings_manager_does_not_overwrite_existing_file(tmp_path):
    (tmp_path / "settings.json").write_text('{"backend": "ollama"}')
    mgr = SettingsManager(config_dir=tmp_path)
    mgr.load()
    saved = json.loads(mgr.settings_path.read_text(encoding="utf-8"))
    assert saved == {"backend": "ollama"}


def test_transformers_small_model_roundtrip(tmp_path):
    mgr = SettingsManager(config_dir=tmp_path)
    settings = mgr.get_defaults()
    settings["transformers"]["model"] = "zai-org/GLM-4.6V-Flash"
    settings["transformers"]["small_model"] = "acme/tiny"
    settings["transformers"]["is_multi_model"] = True
    cfg = mgr.to_appconfig(settings)
    assert cfg.transformers_model == "zai-org/GLM-4.6V-Flash"
    assert cfg.transformers_small_model == "acme/tiny"
    assert cfg.transformers_is_multi_model is True
    back = mgr.from_appconfig(cfg)
    assert back["transformers"]["model"] == "zai-org/GLM-4.6V-Flash"
    assert back["transformers"]["small_model"] == "acme/tiny"
    assert back["transformers"]["is_multi_model"] is True


def test_to_appconfig_defaults_match_default_settings(tmp_path):
    mgr = SettingsManager(config_dir=tmp_path)
    defaults = mgr.get_defaults()
    cfg = mgr.to_appconfig(defaults)
    assert cfg.openai_model == defaults["openai"]["large_model"]
    assert cfg.openai_small_model == defaults["openai"]["small_model"]
    assert cfg.huggingface_model == defaults["huggingface"]["large_model"]
    assert cfg.huggingface_small_model == defaults["huggingface"]["small_model"]
    assert cfg.ollama_model == defaults["ollama"]["large_model"]


def test_huggingface_remote_roundtrip(tmp_path):
    mgr = SettingsManager(config_dir=tmp_path)
    settings = mgr.get_defaults()
    settings["huggingface"]["remote"] = True
    cfg = mgr.to_appconfig(settings)
    assert cfg.huggingface_remote is True
    back = mgr.from_appconfig(cfg)
    assert back["huggingface"]["remote"] is True


def test_huggingface_remote_env_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("OLI_HUGGINGFACE_REMOTE", "true")
    mgr = SettingsManager(config_dir=tmp_path)
    settings = mgr.load()
    cfg = mgr.to_appconfig(settings)
    assert cfg.huggingface_remote is True


def test_agent_pool_size_roundtrip(tmp_path):
    mgr = SettingsManager(config_dir=tmp_path)
    settings = mgr.get_defaults()
    settings["model_params"]["use_agent_pool"] = True
    settings["model_params"]["agent_pool_size"] = 12
    cfg = mgr.to_appconfig(settings)
    assert cfg.use_agent_pool is True
    assert cfg.agent_pool_size == 12
    back = mgr.from_appconfig(cfg)
    assert back["model_params"]["use_agent_pool"] is True
    assert back["model_params"]["agent_pool_size"] == 12


def test_logging_section_roundtrip(tmp_path):
    mgr = SettingsManager(config_dir=tmp_path)
    settings = mgr.get_defaults()
    settings["logging"]["log_level"] = "DEBUG"
    settings["logging"]["log_file"] = "custom/path.ndjson"
    cfg = mgr.to_appconfig(settings)
    assert cfg.log_level == "DEBUG"
    assert cfg.log_file == "custom/path.ndjson"
    back = mgr.from_appconfig(cfg)
    assert back["logging"]["log_level"] == "DEBUG"
    assert back["logging"]["log_file"] == "custom/path.ndjson"


def test_api_server_section_roundtrip(tmp_path):
    mgr = SettingsManager(config_dir=tmp_path)
    settings = mgr.get_defaults()
    settings["api_server"]["host"] = "127.0.0.1"
    settings["api_server"]["port"] = 9001
    settings["api_server"]["profile"] = "analyst"
    settings["api_server"]["mode"] = "ask"
    cfg = mgr.to_appconfig(settings)
    assert cfg.api_host == "127.0.0.1"
    assert cfg.api_port == 9001
    assert cfg.api_profile == "analyst"
    assert cfg.api_mode == "ask"
    back = mgr.from_appconfig(cfg)
    assert back["api_server"]["host"] == "127.0.0.1"
    assert back["api_server"]["port"] == 9001
    assert back["api_server"]["profile"] == "analyst"
    assert back["api_server"]["mode"] == "ask"


def test_paths_section_roundtrip(tmp_path):
    mgr = SettingsManager(config_dir=tmp_path)
    settings = mgr.get_defaults()
    settings["paths"]["profiles_dir"] = "custom_profiles"
    settings["paths"]["logs_dir"] = "custom_logs"
    cfg = mgr.to_appconfig(settings)
    assert cfg.profiles_dir == "custom_profiles"
    assert cfg.logs_dir == "custom_logs"
    back = mgr.from_appconfig(cfg)
    assert back["paths"]["profiles_dir"] == "custom_profiles"
    assert back["paths"]["logs_dir"] == "custom_logs"


def test_env_to_settings_covers_all_appconfig_fields(tmp_path):
    """Drift guard: every AppConfig field must be reachable via DEFAULT_SETTINGS."""
    mgr = SettingsManager(config_dir=tmp_path)
    defaults = mgr.get_defaults()
    cfg = mgr.to_appconfig(defaults)
    # Round-trip through from_appconfig — every field must land somewhere.
    dumped = mgr.from_appconfig(cfg)
    reloaded = mgr.to_appconfig(dumped)
    for field_name in AppConfig.model_fields:
        assert getattr(reloaded, field_name) == getattr(
            cfg, field_name
        ), f"Field {field_name} does not survive from_appconfig/to_appconfig round-trip"
