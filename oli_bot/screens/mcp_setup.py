from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, RadioButton, RadioSet

logger = logging.getLogger(__name__)


class MCPSetupScreen(ModalScreen[Optional[Dict[str, Any]]]):
    CSS = """
    #mcp-setup-container {
        align: center middle;
        width: 60;
        height: auto;
        border: round $primary;
        background: $surface;
        padding: 1;
    }
    #mcp-setup-title {
        text-style: bold;
        content-align: center middle;
        padding: 1 0;
    }
    .mcp-input {
        margin: 0 0 1 0;
    }
    #mcp-buttons {
        height: 3;
        align: center middle;
    }
    Button {
        margin: 0 1;
    }
    #mcp-transport-label {
        padding: 0 0 0 0;
        margin-bottom: 0;
    }
    RadioSet {
        margin: 0 0 1 0;
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
    #mcp-stdio-fields { display: none; height: auto; }
    #mcp-http-fields { display: none; height: auto; }
    .transport-stdio #mcp-stdio-fields { display: block; }
    .transport-http #mcp-http-fields { display: block; }
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, existing: Optional[Dict[str, Any]] = None, **kwargs: Any) -> None:
        """Create an MCP setup screen.

        Parameters
        ----------
        existing:
            When provided the form is pre-populated for editing an existing
            server.  The dict should contain the same keys that ``dismiss``
            produces (``name``, ``transport``, and transport-specific fields).
            The name field is made read-only in edit mode so the server
            identity cannot be accidentally changed.
        """
        super().__init__(**kwargs)
        self._existing = existing

    def compose(self) -> ComposeResult:
        is_edit = self._existing is not None
        title = "Edit MCP Server" if is_edit else "Add MCP Server"
        with Container(id="mcp-setup-container") as self._form:
            yield Label(title, id="mcp-setup-title")
            yield Input(
                placeholder="Server name (e.g., filesystem)",
                id="mcp-name",
                classes="mcp-input",
                disabled=is_edit,
            )
            yield Label("Transport:", id="mcp-transport-label")
            yield RadioSet(
                RadioButton("stdio", id="transport-stdio"),
                RadioButton("http", id="transport-http"),
                id="mcp-transport",
            )
            with Container(id="mcp-http-fields"):
                yield Input(
                    placeholder="URL (e.g., http://localhost:3000/mcp)",
                    id="mcp-url",
                    classes="mcp-input",
                )
            with Container(id="mcp-stdio-fields"):
                yield Input(
                    placeholder="Command (e.g., python, npx)",
                    id="mcp-cmd",
                    classes="mcp-input",
                )
                yield Input(
                    placeholder="Arguments (e.g., -m mcp_server_filesystem /tmp)",
                    id="mcp-args",
                    classes="mcp-input",
                )
                yield Input(
                    placeholder="Env vars (KEY=VALUE ... optional)",
                    id="mcp-env",
                    classes="mcp-input",
                )
            with Horizontal(id="mcp-buttons"):
                yield Button("Save", variant="primary", id="mcp-save")
                yield Button("Cancel", id="mcp-cancel")

    def on_mount(self) -> None:
        rs = self.query_one("#mcp-transport", RadioSet)
        existing = self._existing

        if existing is None:
            # Default to stdio for new servers
            rs.index = 0
            self._form.classes = "transport-stdio"
            return

        # Pre-populate fields for edit mode
        transport = existing.get("transport", "stdio")

        self.query_one("#mcp-name", Input).value = existing.get("name", "")

        if transport == "http":
            rs.index = 1
            self._form.classes = "transport-http"
            self.query_one("#mcp-url", Input).value = existing.get("url", "")
        else:
            rs.index = 0
            self._form.classes = "transport-stdio"
            self.query_one("#mcp-cmd", Input).value = existing.get("command", "")
            args = existing.get("args", [])
            self.query_one("#mcp-args", Input).value = " ".join(args) if args else ""
            env = existing.get("env") or {}
            self.query_one("#mcp-env", Input).value = " ".join(
                f"{k}={v}" for k, v in env.items()
            )

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        transport = event.pressed.label if event.pressed else "stdio"
        self._form.classes = f"transport-{transport}"

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "mcp-cancel":
            self.dismiss(None)
        elif event.button.id == "mcp-save":
            name = self.query_one("#mcp-name", Input).value.strip()
            rs = self.query_one("#mcp-transport", RadioSet)
            idx = rs.pressed_index
            transport = str(rs.children[idx].label) if idx is not None else "stdio"

            if not name:
                self._show_error("Server name is required")
                return

            if transport == "http":
                url = self.query_one("#mcp-url", Input).value.strip()
                if not url:
                    self._show_error("URL is required for HTTP transport")
                    return
                self.dismiss(
                    {
                        "name": name,
                        "transport": "http",
                        "url": url,
                    }
                )
            else:
                cmd = self.query_one("#mcp-cmd", Input).value.strip()
                args_str = self.query_one("#mcp-args", Input).value.strip()
                env_str = self.query_one("#mcp-env", Input).value.strip()

                if not cmd:
                    self._show_error("Command is required for stdio transport")
                    return

                args = args_str.split() if args_str else []
                env = {}
                if env_str:
                    for pair in env_str.split():
                        if "=" in pair:
                            k, v = pair.split("=", 1)
                            env[k] = v

                self.dismiss(
                    {
                        "name": name,
                        "transport": "stdio",
                        "command": cmd,
                        "args": args,
                        "env": env,
                    }
                )

    def _show_error(self, msg: str) -> None:
        try:
            self.query_one("#mcp-error").remove()
        except Exception:
            logger.debug("Failed to remove existing MCP error label")
        self.mount(
            Label(f"[red]{msg}[/red]", id="mcp-error"),
            before="#mcp-buttons",
        )

    def action_cancel(self) -> None:
        self.dismiss(None)


__all__ = ["MCPSetupScreen"]
