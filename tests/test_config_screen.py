"""Tests for ConfigScreen model input rendering and save round-trip."""

from textual.app import App

from oli_bot.screens import ConfigScreen


def _settings() -> dict:
    return {
        "backend": "ollama",
        "openai": {"large_model": "openai-lg", "small_model": "openai-sm"},
        "ollama": {"large_model": "ollama-lg", "small_model": "ollama-sm"},
        "huggingface": {"large_model": "hf-lg", "small_model": "hf-sm"},
        "transformers": {
            "model": "tr-lg",
            "small_model": "tr-sm",
            "device": "auto",
            "dtype": "auto",
        },
        "model_params": {},
        "workspace": {},
        "session": {},
    }


class _HostApp(App):
    def __init__(self, settings: dict):
        super().__init__()
        self._settings = settings
        self.result = None

    def compose(self):
        return []

    def on_mount(self):
        self.push_screen(
            ConfigScreen(self._settings),
            callback=lambda result: setattr(self, "result", result),
        )


async def test_config_screen_populates_backend_model_fields():
    settings = _settings()
    app = _HostApp(settings)
    async with app.run_test() as pilot:
        screen = app.screen
        assert screen.query_one("#cfg-ollama-large-model").value == "ollama-lg"
        assert screen.query_one("#cfg-ollama-small-model").value == "ollama-sm"
        assert screen.query_one("#cfg-openai-large-model").value == "openai-lg"
        assert screen.query_one("#cfg-hf-large-model").value == "hf-lg"
        assert screen.query_one("#cfg-tr-model").value == "tr-lg"
        assert screen.query_one("#cfg-tr-small-model").value == "tr-sm"


async def test_config_screen_save_keeps_transformers_small_model():
    app = _HostApp(_settings())
    async with app.run_test() as pilot:
        screen = app.screen
        screen._save()
        await pilot.pause()
        result = app.result
        assert result["transformers"]["model"] == "tr-lg"
        assert result["transformers"]["small_model"] == "tr-sm"
        assert result["ollama"]["large_model"] == "ollama-lg"
        assert result["ollama"]["small_model"] == "ollama-sm"


async def test_config_screen_renders_new_sections():
    settings = _settings()
    settings["openai"]["vision_style"] = "bedrock"
    settings["huggingface"]["remote"] = True
    settings["transformers"]["is_multi_model"] = True
    settings["model_params"] = {"use_agent_pool": True, "agent_pool_size": 8}
    settings["logging"] = {"log_level": "DEBUG", "log_file": "logs/x.ndjson"}
    settings["api_server"] = {
        "host": "127.0.0.1",
        "port": 9001,
        "profile": "analyst",
        "mode": "ask",
    }
    settings["paths"] = {"profiles_dir": "pf", "logs_dir": "lg"}
    settings["session"] = {"auto_save": False, "resume_prompt": False}
    app = _HostApp(settings)
    async with app.run_test() as pilot:
        screen = app.screen
        await pilot.pause()
        assert screen.query_one("#cfg-use-agent-pool").value is True
        assert screen.query_one("#cfg-agent-pool-size").value == "8"
        assert screen.query_one("#cfg-log-file").value == "logs/x.ndjson"
        assert screen.query_one("#cfg-api-host").value == "127.0.0.1"
        assert screen.query_one("#cfg-api-port").value == "9001"
        assert screen.query_one("#cfg-api-profile").value == "analyst"
        assert screen.query_one("#cfg-api-mode").value == "ask"
        assert screen.query_one("#cfg-profiles-dir").value == "pf"
        assert screen.query_one("#cfg-logs-dir").value == "lg"
        assert screen.query_one("#cfg-auto-save").value is False
        assert screen.query_one("#cfg-resume-prompt").value is False
        assert screen.query_one("#cfg-tr-is-multi-model").value is True
        assert screen.query_one("#cfg-hf-remote").value is True
        # Vision style + log level RadioSets should be positioned correctly.
        assert screen.query_one("#cfg-openai-vision-style").pressed_index == 1
        assert screen.query_one("#cfg-log-level").pressed_index == 0


async def test_config_screen_save_round_trips_new_sections():
    settings = _settings()
    settings["openai"]["vision_style"] = "bedrock"
    settings["huggingface"]["remote"] = True
    settings["transformers"]["is_multi_model"] = True
    settings["model_params"] = {"use_agent_pool": True, "agent_pool_size": 7}
    settings["logging"] = {"log_level": "WARNING", "log_file": "logs/z.ndjson"}
    settings["api_server"] = {
        "host": "10.0.0.1",
        "port": 8123,
        "profile": "bug-hunter",
        "mode": "plan",
    }
    settings["paths"] = {"profiles_dir": "P", "logs_dir": "L"}
    settings["session"] = {"auto_save": False, "resume_prompt": False}
    app = _HostApp(settings)
    async with app.run_test() as pilot:
        screen = app.screen
        await pilot.pause()
        screen._save()
        await pilot.pause()
        result = app.result
        assert result["openai"]["vision_style"] == "bedrock"
        assert result["huggingface"]["remote"] is True
        assert result["transformers"]["is_multi_model"] is True
        assert result["model_params"]["use_agent_pool"] is True
        assert result["model_params"]["agent_pool_size"] == 7
        assert result["logging"]["log_level"] == "WARNING"
        assert result["logging"]["log_file"] == "logs/z.ndjson"
        assert result["api_server"]["host"] == "10.0.0.1"
        assert result["api_server"]["port"] == 8123
        assert result["api_server"]["profile"] == "bug-hunter"
        assert result["api_server"]["mode"] == "plan"
        assert result["paths"]["profiles_dir"] == "P"
        assert result["paths"]["logs_dir"] == "L"
        assert result["session"]["auto_save"] is False
        assert result["session"]["resume_prompt"] is False
