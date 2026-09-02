from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, RadioButton, RadioSet


class ConfigScreen(ModalScreen[dict | None]):
    """Configure model params, backend, models, workspace, and session settings."""

    CSS = """
    #config-container {
        align: center middle;
        width: 72;
        height: auto;
        max-height: 90%;
        border: round $primary;
        background: $surface;
    }

    #config-title {
        padding: 1;
        text-style: bold;
        content-align: center middle;
    }

    #config-scroll {
        height: 1fr;
        padding: 0 1;
        overflow-y: auto;
    }

    .section-title {
        text-style: bold;
        padding: 1 0 0 0;
    }

    .config-input {
        margin: 0 0 1 0;
    }

    .config-label {
        padding: 0 0 0 0;
        margin-bottom: 0;
    }

    RadioSet {
        border: round #6b7d74;
    }
    RadioSet:focus {
        border: round $primary;
    }

    Input {
        border: round #6b7d74;
    }
    Input:focus {
        border: round $primary;
    }

    Checkbox {
        border: round #6b7d74;
    }

    #config-buttons {
        height: auto;
        align: center middle;
        padding: 1;
    }

    Button {
        margin: 0 1;
        min-width: 12;
    }

    Button#config-cancel {
        background: $surface;
        border: round $border;
    }

    .backend-openai #openai-section { display: block; }
    .backend-openai #ollama-section { display: none; }
    .backend-openai #huggingface-section { display: none; }
    .backend-openai #transformers-section { display: none; }
    .backend-ollama #openai-section { display: none; }
    .backend-ollama #ollama-section { display: block; }
    .backend-ollama #huggingface-section { display: none; }
    .backend-ollama #transformers-section { display: none; }
    .backend-huggingface #openai-section { display: none; }
    .backend-huggingface #ollama-section { display: none; }
    .backend-huggingface #huggingface-section { display: block; }
    .backend-huggingface #transformers-section { display: none; }
    .backend-transformers #openai-section { display: none; }
    .backend-transformers #ollama-section { display: none; }
    .backend-transformers #huggingface-section { display: none; }
    .backend-transformers #transformers-section { display: block; }
    #openai-section { display: none; height: auto; }
    #ollama-section { display: block; height: auto; }
    #huggingface-section { display: none; height: auto; }
    #transformers-section { display: none; height: auto; }
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, settings: dict):
        super().__init__()
        self._settings = settings

    def compose(self) -> ComposeResult:
        s = self._settings
        op = s.get("openai", {})
        ol = s.get("ollama", {})
        hf = s.get("huggingface", {})
        tr = s.get("transformers", {})
        mp = s.get("model_params", {})
        ws = s.get("workspace", {})
        ss = s.get("session", {})

        with Container(id="config-container") as self._form:
            yield Label("Configuration", id="config-title")
            with VerticalScroll(id="config-scroll"):
                yield Label("Backend", classes="section-title")
                yield RadioSet(
                    RadioButton("ollama", id="be-ollama"),
                    RadioButton("openai", id="be-openai"),
                    RadioButton("huggingface", id="be-huggingface"),
                    RadioButton("transformers", id="be-transformers"),
                    id="config-backend",
                )

                yield Label("", classes="section-title")
                yield Label(
                    "OpenAI", id="openai-section-title", classes="section-title"
                )
                with Container(id="openai-section"):
                    yield Input(
                        placeholder="API Key",
                        id="cfg-openai-api-key",
                        classes="config-input",
                        password=True,
                        value=op.get("api_key", ""),
                    )
                    yield Input(
                        placeholder="Base URL",
                        id="cfg-openai-base-url",
                        classes="config-input",
                        value=op.get("base_url", "https://api.openai.com/v1"),
                    )
                    yield Input(
                        placeholder="Large model",
                        id="cfg-openai-large-model",
                        classes="config-input",
                        value=op.get("large_model", "gpt-5"),
                    )
                    yield Input(
                        placeholder="Small model",
                        id="cfg-openai-small-model",
                        classes="config-input",
                        value=op.get("small_model", "gpt-5-mini"),
                    )
                    yield Label("Vision style", classes="config-label")
                    yield RadioSet(
                        RadioButton("openai", id="vs-openai"),
                        RadioButton("bedrock", id="vs-bedrock"),
                        id="cfg-openai-vision-style",
                    )

                yield Label(
                    "Ollama", id="ollama-section-title", classes="section-title"
                )
                with Container(id="ollama-section"):
                    yield Input(
                        placeholder="Base URL",
                        id="cfg-ollama-base-url",
                        classes="config-input",
                        value=ol.get("base_url", "http://localhost:11434"),
                    )
                    yield Input(
                        placeholder="Large model",
                        id="cfg-ollama-large-model",
                        classes="config-input",
                        value=ol.get("large_model", ""),
                    )
                    yield Input(
                        placeholder="Small model",
                        id="cfg-ollama-small-model",
                        classes="config-input",
                        value=ol.get("small_model", ""),
                    )

                yield Label(
                    "HuggingFace",
                    id="huggingface-section-title",
                    classes="section-title",
                )
                with Container(id="huggingface-section"):
                    yield Checkbox(
                        "Remote inference (HuggingFace Inference API)",
                        id="cfg-hf-remote",
                        value=bool(hf.get("remote", False)),
                    )
                    yield Input(
                        placeholder="Base URL",
                        id="cfg-hf-base-url",
                        classes="config-input",
                        value=hf.get(
                            "base_url", "https://api-inference.huggingface.co"
                        ),
                    )
                    yield Input(
                        placeholder="API Key",
                        id="cfg-hf-api-key",
                        classes="config-input",
                        password=True,
                        value=hf.get("api_key", ""),
                    )
                    yield Input(
                        placeholder="Large model",
                        id="cfg-hf-large-model",
                        classes="config-input",
                        value=hf.get("large_model", ""),
                    )
                    yield Input(
                        placeholder="Small model",
                        id="cfg-hf-small-model",
                        classes="config-input",
                        value=hf.get("small_model", ""),
                    )

                yield Label(
                    "Transformers",
                    id="transformers-section-title",
                    classes="section-title",
                )
                with Container(id="transformers-section"):
                    yield Input(
                        placeholder="Model name or path",
                        id="cfg-tr-model",
                        classes="config-input",
                        value=tr.get("model", ""),
                    )
                    yield Input(
                        placeholder="Small model",
                        id="cfg-tr-small-model",
                        classes="config-input",
                        value=tr.get("small_model", ""),
                    )
                    yield Input(
                        placeholder="Device (auto, cuda, cpu)",
                        id="cfg-tr-device",
                        classes="config-input",
                        value=tr.get("device", "auto"),
                    )
                    yield Input(
                        placeholder="Dtype (auto, float16, bfloat16, float32)",
                        id="cfg-tr-dtype",
                        classes="config-input",
                        value=tr.get("dtype", "auto"),
                    )
                    yield Checkbox(
                        "Multi-model (route between large/small at runtime)",
                        id="cfg-tr-is-multi-model",
                        value=bool(tr.get("is_multi_model", False)),
                    )

                yield Label("Model Parameters", classes="section-title")
                yield Input(
                    placeholder=f"Max tokens ({mp.get('max_tokens', 2048)})",
                    id="cfg-max-tokens",
                    classes="config-input",
                    value=str(mp.get("max_tokens", 2048)),
                )
                yield Input(
                    placeholder=f"Temperature ({mp.get('temperature', 0.7)})",
                    id="cfg-temperature",
                    classes="config-input",
                    value=str(mp.get("temperature", 0.7)),
                )
                yield Input(
                    placeholder=f"Max retries ({mp.get('max_retries', 3)})",
                    id="cfg-max-retries",
                    classes="config-input",
                    value=str(mp.get("max_retries", 3)),
                )
                yield Input(
                    placeholder=f"Retry delay ({mp.get('retry_delay', 1.0)})",
                    id="cfg-retry-delay",
                    classes="config-input",
                    value=str(mp.get("retry_delay", 1.0)),
                )
                yield Input(
                    placeholder=f"Request timeout ({mp.get('request_timeout', 30.0)})",
                    id="cfg-request-timeout",
                    classes="config-input",
                    value=str(mp.get("request_timeout", 30.0)),
                )
                yield Input(
                    placeholder=f"Max messages ({mp.get('max_messages', 100)})",
                    id="cfg-max-messages",
                    classes="config-input",
                    value=str(mp.get("max_messages", 100)),
                )
                yield Input(
                    placeholder=f"Max tool iterations ({mp.get('max_tool_iterations', 25)})",
                    id="cfg-max-tool-iterations",
                    classes="config-input",
                    value=str(mp.get("max_tool_iterations", 25)),
                )
                yield Input(
                    placeholder=f"Stream timeout ({mp.get('stream_timeout', 240.0)})",
                    id="cfg-stream-timeout",
                    classes="config-input",
                    value=str(mp.get("stream_timeout", 240.0)),
                )
                yield Input(
                    placeholder="Model filters (comma-sep, e.g. :cloud,-cloud)",
                    id="cfg-model-filters",
                    classes="config-input",
                    value=mp.get("model_filters", ""),
                )

                yield Label("Tool Result Truncation", classes="section-title")
                yield Input(
                    placeholder=f"Small model max chars ({mp.get('truncation_max_chars_small', 4000)})",
                    id="cfg-truncation-small",
                    classes="config-input",
                    value=str(mp.get("truncation_max_chars_small", 4000)),
                )
                yield Input(
                    placeholder=f"Large model max chars ({mp.get('truncation_max_chars_large', 100000)})",
                    id="cfg-truncation-large",
                    classes="config-input",
                    value=str(mp.get("truncation_max_chars_large", 100000)),
                )

                yield Label("Offline Mode", classes="section-title")
                yield Checkbox(
                    "Block network tools (web search, fetch, etc.)",
                    id="cfg-offline-mode",
                    value=bool(mp.get("offline_mode", True)),
                )

                yield Label("Dry Run", classes="section-title")
                yield Checkbox(
                    "Preview destructive actions without executing",
                    id="cfg-dry-run",
                    value=bool(mp.get("dry_run", False)),
                )

                yield Label("Agent Pool", classes="section-title")
                yield Checkbox(
                    "Enable agent pool (root can dispatch to agents.yaml sub-agents)",
                    id="cfg-use-agent-pool",
                    value=bool(mp.get("use_agent_pool", False)),
                )
                yield Input(
                    placeholder=f"Agent pool size ({mp.get('agent_pool_size', 5)})",
                    id="cfg-agent-pool-size",
                    classes="config-input",
                    value=str(mp.get("agent_pool_size", 5)),
                )

                lg = s.get("logging", {})
                yield Label("Logging", classes="section-title")
                yield Label("Log level", classes="config-label")
                yield RadioSet(
                    RadioButton("DEBUG", id="ll-debug"),
                    RadioButton("INFO", id="ll-info"),
                    RadioButton("WARNING", id="ll-warning"),
                    RadioButton("ERROR", id="ll-error"),
                    id="cfg-log-level",
                )
                yield Input(
                    placeholder=f"Log file ({lg.get('log_file', 'logs/backend.ndjson')})",
                    id="cfg-log-file",
                    classes="config-input",
                    value=lg.get("log_file", "logs/backend.ndjson"),
                )

                api = s.get("api_server", {})
                yield Label("API Server", classes="section-title")
                yield Input(
                    placeholder=f"Host ({api.get('host', '0.0.0.0')})",
                    id="cfg-api-host",
                    classes="config-input",
                    value=api.get("host", "0.0.0.0"),
                )
                yield Input(
                    placeholder=f"Port ({api.get('port', 8000)})",
                    id="cfg-api-port",
                    classes="config-input",
                    value=str(api.get("port", 8000)),
                )
                yield Input(
                    placeholder=f"Profile ({api.get('profile', 'default')})",
                    id="cfg-api-profile",
                    classes="config-input",
                    value=api.get("profile", "default"),
                )
                yield Input(
                    placeholder=f"Mode ({api.get('mode', 'agent')})",
                    id="cfg-api-mode",
                    classes="config-input",
                    value=api.get("mode", "agent"),
                )

                paths = s.get("paths", {})
                yield Label("Paths", classes="section-title")
                yield Input(
                    placeholder=f"Profiles dir ({paths.get('profiles_dir', 'profiles')})",
                    id="cfg-profiles-dir",
                    classes="config-input",
                    value=paths.get("profiles_dir", "profiles"),
                )
                yield Input(
                    placeholder=f"Logs dir ({paths.get('logs_dir', 'logs')})",
                    id="cfg-logs-dir",
                    classes="config-input",
                    value=paths.get("logs_dir", "logs"),
                )

                yield Label("Session & Workspace", classes="section-title")
                yield Checkbox(
                    "Auto-save session after every response",
                    id="cfg-auto-save",
                    value=bool(ss.get("auto_save", True)),
                )
                yield Checkbox(
                    "Prompt to resume last session on startup",
                    id="cfg-resume-prompt",
                    value=bool(ss.get("resume_prompt", True)),
                )
                yield Input(
                    placeholder=f"Max workspaces ({ws.get('max_workspaces', 20)})",
                    id="cfg-max-workspaces",
                    classes="config-input",
                    value=str(ws.get("max_workspaces", 20)),
                )

            with Horizontal(id="config-buttons"):
                yield Button("Save", variant="primary", id="config-save")
                yield Button("Cancel", id="config-cancel")

    def on_mount(self) -> None:
        rs = self.query_one("#config-backend", RadioSet)
        backend = self._settings.get("backend", "ollama")
        backend_indices = {
            "ollama": 0,
            "openai": 1,
            "huggingface": 2,
            "transformers": 3,
        }
        self._press_radio(rs, backend_indices.get(backend, 0))
        self._update_sections(backend)

        vs = self.query_one("#cfg-openai-vision-style", RadioSet)
        vision_style = self._settings.get("openai", {}).get("vision_style", "openai")
        self._press_radio(vs, 1 if vision_style == "bedrock" else 0)

        ll = self.query_one("#cfg-log-level", RadioSet)
        log_level = self._settings.get("logging", {}).get("log_level", "INFO").upper()
        self._press_radio(
            ll, {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3}.get(log_level, 1)
        )

    @staticmethod
    def _press_radio(rs: RadioSet, index: int) -> None:
        buttons = list(rs.query(RadioButton))
        if 0 <= index < len(buttons):
            buttons[index].value = True
            rs.index = index

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        if event.radio_set.id != "config-backend":
            return
        backend = event.pressed.label if event.pressed else "ollama"
        self._update_sections(backend)

    def _update_sections(self, backend: str) -> None:
        self._form.classes = f"backend-{backend}"

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "config-cancel":
            self.dismiss(None)
        elif event.button.id == "config-save":
            self._save()

    def _save(self) -> None:
        rs = self.query_one("#config-backend", RadioSet)
        backend = self._radio_label(rs, "ollama")

        vs = self.query_one("#cfg-openai-vision-style", RadioSet)
        vision_style = self._radio_label(vs, "openai")

        ll = self.query_one("#cfg-log-level", RadioSet)
        log_level = self._radio_label(ll, "INFO")

        settings = {
            "backend": backend,
            "openai": {
                "api_key": self._val("#cfg-openai-api-key"),
                "base_url": self._val("#cfg-openai-base-url"),
                "large_model": self._val("#cfg-openai-large-model"),
                "small_model": self._val("#cfg-openai-small-model"),
                "vision_style": vision_style,
            },
            "ollama": {
                "base_url": self._val("#cfg-ollama-base-url"),
                "large_model": self._val("#cfg-ollama-large-model"),
                "small_model": self._val("#cfg-ollama-small-model"),
            },
            "huggingface": {
                "base_url": self._val("#cfg-hf-base-url"),
                "api_key": self._val("#cfg-hf-api-key"),
                "large_model": self._val("#cfg-hf-large-model"),
                "small_model": self._val("#cfg-hf-small-model"),
                "remote": self._bool("#cfg-hf-remote"),
            },
            "transformers": {
                "model": self._val("#cfg-tr-model"),
                "small_model": self._val("#cfg-tr-small-model"),
                "device": self._val("#cfg-tr-device"),
                "dtype": self._val("#cfg-tr-dtype"),
                "is_multi_model": self._bool("#cfg-tr-is-multi-model"),
            },
            "model_params": {
                "max_tokens": self._int("#cfg-max-tokens", 2048),
                "temperature": self._float("#cfg-temperature", 0.7),
                "max_retries": self._int("#cfg-max-retries", 3),
                "retry_delay": self._float("#cfg-retry-delay", 1.0),
                "request_timeout": self._float("#cfg-request-timeout", 30.0),
                "max_messages": self._int("#cfg-max-messages", 100),
                "max_tool_iterations": self._int("#cfg-max-tool-iterations", 25),
                "stream_timeout": self._float("#cfg-stream-timeout", 240.0),
                "model_filters": self._val("#cfg-model-filters"),
                "truncation_max_chars_small": self._int("#cfg-truncation-small", 4000),
                "truncation_max_chars_large": self._int(
                    "#cfg-truncation-large", 100000
                ),
                "offline_mode": self._bool("#cfg-offline-mode"),
                "dry_run": self._bool("#cfg-dry-run"),
                "use_agent_pool": self._bool("#cfg-use-agent-pool"),
                "agent_pool_size": self._int("#cfg-agent-pool-size", 5),
            },
            "logging": {
                "log_level": log_level,
                "log_file": self._val("#cfg-log-file"),
            },
            "api_server": {
                "host": self._val("#cfg-api-host"),
                "port": self._int("#cfg-api-port", 8000),
                "profile": self._val("#cfg-api-profile"),
                "mode": self._val("#cfg-api-mode"),
            },
            "paths": {
                "profiles_dir": self._val("#cfg-profiles-dir"),
                "logs_dir": self._val("#cfg-logs-dir"),
            },
            "workspace": {
                "max_workspaces": self._int("#cfg-max-workspaces", 20),
            },
            "session": {
                "auto_save": self._bool("#cfg-auto-save"),
                "resume_prompt": self._bool("#cfg-resume-prompt"),
            },
        }
        self.dismiss(settings)

    def _val(self, selector: str) -> str:
        return self.query_one(selector, Input).value.strip()

    def _int(self, selector: str, default: int) -> int:
        try:
            return int(self._val(selector) or str(default))
        except ValueError:
            return default

    def _float(self, selector: str, default: float) -> float:
        try:
            return float(self._val(selector) or str(default))
        except ValueError:
            return default

    def _bool(self, selector: str) -> bool:
        return self.query_one(selector, Checkbox).value

    @staticmethod
    def _radio_label(rs: RadioSet, default: str) -> str:
        buttons = list(rs.query(RadioButton))
        idx = rs.pressed_index
        if idx is None or idx < 0 or idx >= len(buttons):
            return default
        return str(buttons[idx].label)

    def action_cancel(self) -> None:
        self.dismiss(None)


__all__ = ["ConfigScreen"]
