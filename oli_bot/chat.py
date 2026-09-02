#!/usr/bin/env python3
"""
oli — a Multi-Backend AI Chat Agent with a Textual-based TUI chat interface.
"""

import argparse
import asyncio
import logging
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from art import text2art
from rich import box
from rich.console import Group
from rich.markdown import Markdown
from rich.markup import escape as rich_escape
from rich.padding import Padding
from rich.panel import Panel
from rich.table import Table
from rich.text import Text as RichText
from textual import events, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import Click
from textual.widgets import (
    Collapsible,
    Input,
    Label,
    ListItem,
    ListView,
    Static,
    Tree,
)

from .backends import Message, create_model_backend
from .agent import (
    Agent,
    AgentPool,
    AssistantResponse,
    Done as AgentDone,
    Error as AgentError,
    StreamChunk,
    stream_sub_agent_run,
    sanitize_tool_history,
    ThinkingChunk,
    ToolCallExecuting,
    ToolCallResult,
)

from .config import configs
from .models import SubAgentRun
from .tools.manager import BuiltinToolManager
from .mcp_client import MCPClientManager
from .server_manager import ServerManager
from .sessions import (
    SCOPE_WORKSPACE_SENSITIVE,
    Session,
    ConversationStore,
    WorkspaceManager,
    _message_from_dict,
    is_sensitive_path,
)
from .settings import SettingsManager
from .screens import (
    ModelPicker,
    ServerListScreen,
    WorkspaceListScreen,
    MCPSetupScreen,
    PermissionScreen,
    ConfirmScreen,
    SessionListScreen,
    SubAgentViewScreen,
    ConfigScreen,
    TAGLINES,
)

from .logger import setup_logging

MESSAGE_BOX = box.ROUNDED
PRIMARY_HEX = "#2ecc71"
MUTED_HEX = "#6b7d74"

setup_logging(log_path=configs.log_file)
logger = logging.getLogger(__name__)

# Top-level slash commands, single source of truth for autocomplete
COMMANDS = (
    "/help",
    "/clear",
    "/models",
    "/model",
    "/config",
    "/servers",
    "/mode",
    "/profile",
    "/context",
    "/mcp",
    "/sessions",
    "/workspace",
    "/home",
    "/offline",
    "/dry-run",
)


class OliBot(App):
    CSS = """
    $primary: #2ecc71;
    $secondary: #58d68d;
    $accent: #a9dfbf;
    $surface: #030503;
    $panel: #0a120d;
    $background: #020403;
    $foreground: #d7e4de;
    $text: #d7e4de;
    $text-muted: #6b7d74;
    $boost: #0d1f14;

    Screen {
        background: $background;
        color: $foreground;
    }

    #top-bar {
        height: 1;
        background: $panel;
        color: $text-muted;
        padding: 0 2;
    }

    #content-area {
        height: 1fr;
    }

    #chat-column {
        width: 2fr;
        height: 1fr;
    }

    #chat-log {
        width: 100%;
        height: 1fr;
        margin: 0 0 0 1;
        overflow-y: scroll;
    }

    #sub-agent-tree {
        width: 1fr;
        margin: 0 1 0 0;
        overflow-y: scroll;
        border: solid $primary;
        border-title-color: $primary;
        border-title-style: bold;
    }

    #bottom-bar {
        dock: bottom;
        height: auto;
    }

    #chat-input {
        margin: 1 2;
        background: $panel;
        color: $text;
        border: solid $primary;
    }
    #chat-input:focus { border: solid $accent; }

    #command-suggestions {
        display: none;
        height: auto;
        max-height: 8;
        margin: 0 2;
        background: $panel;
        border: solid $primary;
    }
    #command-suggestions > ListItem.-highlight { background: $boost; color: $accent; }

    #status-bar {
        height: 1;
        background: $panel;
        color: $text-muted;
        padding: 0 2;
    }

    Button {
        background: $panel;
        color: $text;
        border: solid $primary;
    }
    Button:hover  { background: $boost; color: $accent; }
    Button:focus  { text-style: bold; border: solid $accent; }
    Button.-primary {
        background: $primary 30%;
        color: $accent;
        border: solid $primary;
    }

    .message {
        margin: 1 0;
    }
    """

    ROLE_COLORS = {
        "You": "blue",
        "Assistant": "green",
        "System": "yellow",
        "Tool Call": "magenta",
    }

    ROLE_ICONS = {
        "You": "\u276f",
        "Assistant": "\u25cf",
        "System": "\u25aa",
        "Tool Call": "\u25c6",
    }

    BINDINGS = [
        ("ctrl+q", "app.quit", "Quit"),
        ("ctrl+l", "clear_chat", "Clear"),
        ("ctrl+y", "copy_last_message", "Copy"),
    ]

    def __init__(
        self,
        model: str | None,
        base_url: str = "http://localhost:11434",
        profile: str = "default",
        resume_last: bool = False,
        load_session: str | None = None,
        dry_run: bool = False,
        use_pool: bool = False,
    ):
        super().__init__()
        self.settings_manager = SettingsManager()
        self.settings = self.settings_manager.load()
        self.config = self.settings_manager.to_appconfig(self.settings)
        if dry_run:
            self.config.dry_run = True
        if use_pool:
            self.config.use_agent_pool = True
        self.model_size: str = "large"
        self.server_manager = ServerManager()
        self.server_manager.seed_default(base_url)
        active = self.server_manager.get_active()
        effective_url = active.url if active else base_url

        cli_model = model
        if (
            cli_model is None
            and active
            and active.default_model
            and self.config.backend == "ollama"
        ):
            cli_model = active.default_model
        if cli_model is not None:
            self.config.openai_model = cli_model
            self.config.ollama_model = cli_model
            self.config.huggingface_model = cli_model
            self.config.transformers_model = cli_model

        self.backend = create_model_backend(
            effective_url, self.config.backend.lower(), cli_model
        )
        if self.backend.model is None or not self.backend.model:
            self.backend.model = self._get_large_model() or None
        self.messages: list[Message] = []
        cwd = Path.cwd()
        if is_sensitive_path(cwd):
            self.session = Session(workspace=None)
            self._cwd_sensitive = True
        else:
            self.session = Session(workspace=cwd)
            self._cwd_sensitive = False

        self._builtin_tools = BuiltinToolManager(
            session=self.session,
            backend=self.backend,
            config=self.config,
        )
        self.mcp_manager = MCPClientManager(
            builtin_tools=self._builtin_tools,
            offline_mode=self.config.offline_mode,
        )
        # Root agent and main conversation llm
        self.agent = Agent(
            role="root",
            backend=self.backend,
            mcp_manager=self.mcp_manager,
            profile_name=profile,
            config=self.config,
        )
        self._permission_lock = asyncio.Lock()
        self.agent_pool: AgentPool | None = None
        if self.config.use_agent_pool:
            try:
                self.agent_pool = AgentPool(self.mcp_manager)
                self._register_dispatch_tool()
            except Exception as e:
                logger.error("Failed to build agent pool: %s", e)
                self.agent_pool = None

        if self.agent.permission_enforcer is not None:
            self._builtin_tools._permission_enforcer = self.agent.permission_enforcer
        self._builtin_tools.model_tier = self.model_size
        self.store = ConversationStore()
        self.workspace_manager = WorkspaceManager()
        self.current_session_id: str = ""
        self._last_tool_widget: Static | None = None
        self._sub_runs: list[SubAgentRun] = []
        self._sub_tree_nodes: dict = {}
        self._sub_tree_timer = None
        self._command_matches: list[str] = []
        self._suggestion_index: int = 0

        server_name = active.name if active else "default"
        loaded = False
        if load_session:
            data = self.store.load_session(server_name, load_session)
            if data:
                self.messages = sanitize_tool_history(
                    [_message_from_dict(m) for m in data.get("messages", [])]
                )
                self.current_session_id = data["id"]
                if data.get("model"):
                    self.backend.model = data["model"]
                loaded = True
            else:
                logger.warning("Session %s not found, creating new", load_session)
        elif resume_last:
            last_id = self.store.get_last_session(server_name)
            if last_id:
                data = self.store.load_session(server_name, last_id)
                if data:
                    self.messages = sanitize_tool_history(
                        [_message_from_dict(m) for m in data.get("messages", [])]
                    )
                    self.current_session_id = data["id"]
                    if data.get("model"):
                        self.backend.model = data["model"]
                    loaded = True

        if not loaded:
            self.current_session_id = self.store.create_session(
                server=server_name,
                model=self.backend.model or "",
                profile=self.agent.profile_name or "",
                system_prompt=self.agent.system_prompt or "",
            )
            if self.agent.system_prompt:
                self.messages.append(
                    Message(role="system", content=self.agent.system_prompt)
                )

    def compose(self) -> ComposeResult:
        yield Static(id="top-bar")
        with Horizontal(id="content-area"):
            with Vertical(id="chat-column"):
                with VerticalScroll(id="chat-log"):
                    yield self._render_welcome()
            if self.agent_pool is not None:
                yield Tree("Root", id="sub-agent-tree")
        with Vertical(id="bottom-bar"):
            yield ListView(id="command-suggestions")
            yield Input(placeholder="Send a message...", id="chat-input")
            yield Static(id="status-bar")

    def _render_welcome_panel(self) -> Panel:
        active = self.server_manager.get_active()
        backend_label = (
            f"[bold {PRIMARY_HEX}]{self.config.backend}[/bold {PRIMARY_HEX}]"
        )
        if self.config.backend == "ollama":
            server_url = active.url if active else self.backend.base_url
            server_name = f" ({active.name})" if active else ""
            server_row = ("Server", f"{server_url}{server_name}")
        elif self.config.backend == "openai":
            server_row = (
                "API",
                f"[bold {PRIMARY_HEX}]{self.config.openai_base_url}[/bold {PRIMARY_HEX}]",
            )
        elif self.config.backend == "huggingface":
            server_row = (
                "API",
                f"[bold {PRIMARY_HEX}]{self.config.huggingface_base_url}[/bold {PRIMARY_HEX}]",
            )
        elif self.config.backend == "transformers":
            server_row = (
                "Local",
                f"[bold {PRIMARY_HEX}]{self.config.transformers_device}[/bold {PRIMARY_HEX}]",
            )
        else:
            server_row = None
        current_model_label = (
            f"[bold {PRIMARY_HEX}]{self.backend.model}[/bold {PRIMARY_HEX}]"
            if self.backend.model
            else f"[bold {PRIMARY_HEX}]None[/bold {PRIMARY_HEX}]"
        )
        large_model = self._get_large_model() or "—"
        small_model = self._get_small_model() or "—"
        profile = self.agent.profile_name if self.agent.profile_name else "none"
        profile_version = ""
        if self.agent.profile_data is not None:
            m = self.agent.profile_data.manifest
            profile_version = f" v{m.version}"
            if m.base:
                profile_version += f" \\<- {m.base}"
        logo = text2art("oli", font="tarty1").strip()
        ws_label = (
            f"[bold {PRIMARY_HEX}]{self.session.workspace}[/bold {PRIMARY_HEX}]"
            if self.session.workspace
            else "[red]Not set[/red]"
        )

        tagline = rich_escape(random.choice(TAGLINES))
        left = RichText.from_markup(
            f"[bold {PRIMARY_HEX}]{logo}[/bold {PRIMARY_HEX}]\n\n"
            f"[dim]{tagline}[/dim]\n\n"
            "Type a message to start chatting,\n"
            f"or [bold {PRIMARY_HEX}]/help[/bold {PRIMARY_HEX}] for commands."
        )

        rows = [("Backend", backend_label)]
        if server_row is not None:
            rows.append(server_row)
        rows.extend(
            [
                ("Model", f"{current_model_label} [dim]({self.model_size})[/dim]"),
                ("Large", f"[bold {PRIMARY_HEX}]{large_model}[/bold {PRIMARY_HEX}]"),
                ("Small", f"[bold {PRIMARY_HEX}]{small_model}[/bold {PRIMARY_HEX}]"),
                (
                    "Profile",
                    f"[bold {PRIMARY_HEX}]{profile}[/bold {PRIMARY_HEX}]{profile_version}",
                ),
                ("Status", self._status_badges()),
                ("Workspace", ws_label),
                (
                    "Session",
                    f"[bold {PRIMARY_HEX}]{self.current_session_id}[/bold {PRIMARY_HEX}]",
                ),
            ]
        )
        right = Table.grid(padding=(0, 1))
        right.add_column(no_wrap=True)
        right.add_column(ratio=1, overflow="fold")
        for label, value in rows:
            right.add_row(f"[{MUTED_HEX}]{label}[/{MUTED_HEX}]", value)

        grid = Table.grid(padding=(0, 3), expand=True)
        grid.add_column(ratio=2)
        grid.add_column(ratio=3)
        grid.add_row(left, right)

        return Panel(
            grid,
            title="oli",
            title_align="left",
            border_style=PRIMARY_HEX,
            box=MESSAGE_BOX,
        )

    def _render_welcome(self) -> Static:
        return Static(
            self._render_welcome_panel(),
            id="welcome",
            classes="message",
        )

    async def on_unmount(self) -> None:
        self._stop_sub_tree_timer()
        await self.mcp_manager.disconnect_all()

    def on_mount(self) -> None:
        self.update_header()
        if self.agent_pool is not None:
            self.query_one("#sub-agent-tree", Tree).border_title = "Active Sub-Agents"
        self.query_one("#chat-input", Input).focus()
        for w in self.mcp_manager.pop_warnings():
            self.notify(w, severity="warning", timeout=5)
        if getattr(self, "_cwd_sensitive", False):
            self.notify(
                "Workspace not set — current directory is a sensitive location. Use /workspace set to configure.",
                severity="warning",
                timeout=8,
            )
        non_system = [m for m in self.messages if m.role != "system"]
        if non_system:
            self._remove_welcome()
            for msg in non_system:
                if msg.role == "user":
                    self._add_message("You", msg.content, timestamp=msg.timestamp)
                elif msg.role == "assistant":
                    self._add_message("Assistant", msg.content, timestamp=msg.timestamp)

    def on_click(self, event: Click) -> None:
        if event.button != 2:
            return
        widget = event.widget
        if widget and "message" in widget.classes:
            plain_text = getattr(widget, "plain_text", None)
            if plain_text:
                try:
                    clean = RichText.from_markup(plain_text).plain
                except Exception:
                    clean = plain_text
                self.copy_to_clipboard(clean)
                self.notify("Copied to clipboard", timeout=2)

    def _server_name(self) -> str:
        active = self.server_manager.get_active()
        return active.name if active else "default"

    def _save_session(self) -> None:
        if not self.current_session_id:
            return
        new_id = self.store.save_session(
            server=self._server_name(),
            session_id=self.current_session_id,
            messages=self.messages,
            model=self.backend.model or "",
            profile=self.agent.profile_name or "",
        )
        if new_id != self.current_session_id:
            logger.info(
                "Session id changed on save: %s -> %s (previous file was missing/corrupt)",
                self.current_session_id,
                new_id,
            )
            self.current_session_id = new_id

    def update_header(self) -> None:
        active = self.server_manager.get_active()
        server_part = f" {active.name}" if active else ""
        model_part = (
            str(self.backend.model)
            if self.backend.model
            else f"[{PRIMARY_HEX}]None[/{PRIMARY_HEX}]"
        )
        status = Table.grid(expand=True)
        status.add_column(ratio=1)
        status.add_column(justify="right")
        hints = "[dim]^Q[/dim] quit   [dim]^L[/dim] clear   [dim]^Y[/dim] copy"
        info = (
            f"{self._status_badges()}  {model_part} "
            f"[dim]:: {self.agent.profile_name}{server_part}[/dim]"
        )
        status.add_row(hints, info)
        self.query_one("#status-bar", Static).update(status)

        top = Table.grid(expand=True)
        top.add_column(ratio=1)
        top.add_column(justify="right")
        ws_label = (
            str(self.session.workspace)
            if self.session.workspace
            else "no workspace set"
        )
        top.add_row(
            f"[bold {PRIMARY_HEX}]oli[/bold {PRIMARY_HEX}] [dim]· {self.config.backend}[/dim]",
            f"[dim]{ws_label}[/dim]",
        )
        self.query_one("#top-bar", Static).update(top)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._hide_command_suggestions()
        text = event.value.strip()
        if not text:
            return

        self.query_one("#chat-input").value = ""
        self._remove_welcome()

        if text.startswith("/"):
            self._handle_command(text)
        else:
            self._handle_user_message(text)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "chat-input":
            return
        text = event.value
        if text.startswith("/") and not any(c.isspace() for c in text):
            matches = [c for c in COMMANDS if c.startswith(text.lower())]
        else:
            matches = []
        self._update_command_suggestions(matches)

    def on_input_blurred(self, event: Input.Blurred) -> None:
        if event.input.id == "chat-input":
            self._hide_command_suggestions()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id != "command-suggestions":
            return
        idx = event.list_view.index
        if idx is not None and 0 <= idx < len(self._command_matches):
            self._accept_command_suggestion(self._command_matches[idx])

    def on_key(self, event: events.Key) -> None:
        if not self._command_matches:
            return
        suggestions = self.query_one("#command-suggestions", ListView)
        if event.key == "down":
            self._suggestion_index = (self._suggestion_index + 1) % len(
                self._command_matches
            )
            suggestions.index = self._suggestion_index
            event.stop()
            event.prevent_default()
        elif event.key == "up":
            self._suggestion_index = (self._suggestion_index - 1) % len(
                self._command_matches
            )
            suggestions.index = self._suggestion_index
            event.stop()
            event.prevent_default()
        elif event.key == "tab":
            self._accept_command_suggestion(
                self._command_matches[self._suggestion_index]
            )
            event.stop()
            event.prevent_default()
        elif event.key == "escape":
            self._hide_command_suggestions()
            event.stop()
            event.prevent_default()

    def _update_command_suggestions(self, matches: list[str]) -> None:
        if not matches:
            self._hide_command_suggestions()
            return
        self._command_matches = matches
        self._suggestion_index = 0
        suggestions = self.query_one("#command-suggestions", ListView)
        suggestions.clear()
        for cmd in matches:
            suggestions.append(ListItem(Label(cmd)))
        suggestions.index = 0
        suggestions.styles.display = "block"

    def _hide_command_suggestions(self) -> None:
        self._command_matches = []
        self._suggestion_index = 0
        suggestions = self.query_one("#command-suggestions", ListView)
        suggestions.clear()
        suggestions.styles.display = "none"

    def _accept_command_suggestion(self, cmd: str) -> None:
        chat_input = self.query_one("#chat-input", Input)
        chat_input.value = f"{cmd} "
        chat_input.cursor_position = len(chat_input.value)
        self._hide_command_suggestions()
        chat_input.focus()

    # --- Command handling ---

    def _handle_command(self, text: str) -> None:
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()

        if cmd == "/help":
            self._add_message(
                "System",
                "Available commands:\n"
                "  [bold]/models[/bold] [name]          — pick / switch model\n"
                "  [bold]/model large|small[/bold]      — switch between large/small model\n"
                "  [bold]/model add <name> [--tier][/bold] — register a model\n"
                "  [bold]/model remove <name>[/bold]    — unregister a model\n"
                "  [bold]/model list[/bold]             — list registered models\n"
                "  [bold]/model set-large|set-small <name>[/bold] — set per-server large/small model\n"
                "  [bold]/config[/bold]                 — open configuration screen\n"
                "  [bold]/servers add <name> <url>[/bold]     — add Ollama server\n"
                "  [bold]/servers list[/bold]           — list Ollama servers\n"
                "  [bold]/servers remove <name>[/bold]  — remove Ollama server\n"
                "  [bold]/servers default <name>[/bold] — set default server\n"
                "  [bold]/servers switch <name>[/bold]  — switch active server\n"
                "  [bold]/servers <name> use-model <model>[/bold] — set default model for a server\n"
                "  [bold]/mcp add[/bold]               — add MCP server\n"
                "  [bold]/mcp list[/bold]              — list MCP servers\n"
                "  [bold]/mcp remove <name>[/bold]     — remove MCP server\n"
                "  [bold]/mode [ask|agent|chat|plan][/bold] — switch mode (ask=read-only tools, agent=all tools, chat=no tools, plan=research + save a plan)\n"
                "  [bold]/profile list[/bold]           — list available profiles\n"
                "  [bold]/profile load <name>[/bold]    — load a profile\n"
                "  [bold]/profile create <name>[/bold]  — generate a new profile with the current model\n"
                "  [bold]/context[/bold]                — show current server, model, profile\n"
                "  [bold]/sessions[/bold]               — browse sessions interactively\n"
                "  [bold]/sessions list[/bold]         — list saved sessions\n"
                "  [bold]/sessions switch <id>[/bold]  — switch to another session\n"
                "  [bold]/sessions rename <id> <name>[/bold] — rename a session\n"
                "  [bold]/sessions delete <id>[/bold]  — delete a session\n"
                "  [bold]/sessions purge [server][/bold] — delete ALL sessions (optionally for a server)\n"
                "  [bold]/workspace list[/bold]         — list workspaces\n"
                "  [bold]/workspace set <path>[/bold]   — set workspace directory\n"
                "  [bold]/workspace unset[/bold]        — clear workspace\n"
                "  [bold]/clear[/bold]                 — clear conversation\n"
                "  [bold]/offline[/bold]              — toggle offline mode (block network tools)\n"
                "  [bold]/dry-run[/bold]               — toggle dry-run mode (preview destructive actions)\n"
                "  [bold]/home[/bold]                  — go to home screen\n"
                "  [bold]/help[/bold]                  — show this message\n"
                "  [bold]Ctrl+Q[/bold]                 — quit\n"
                "  [bold]Ctrl+L[/bold]                 — clear",
            )
        elif cmd == "/clear":
            self.action_clear_chat()
        elif cmd == "/models":
            if len(parts) > 1:
                self._switch_model(parts[1])
            else:
                self._show_model_picker()
        elif cmd == "/model":
            self._handle_model_switch(text)
        elif cmd == "/config":
            self._handle_config()
        elif cmd == "/servers":
            self._handle_server(text)
        elif cmd == "/mode":
            self._handle_mode(parts)
        elif cmd == "/profile":
            self._handle_profile(text)
        elif cmd == "/context":
            self._handle_context()
        elif cmd == "/mcp":
            self._handle_mcp(text)
        elif cmd == "/sessions":
            self._handle_sessions(text)
        elif cmd == "/home":
            self.action_go_home()
        elif cmd == "/offline":
            self._handle_offline()
        elif cmd == "/dry-run":
            self._handle_dry_run()
        elif cmd == "/workspace":
            self._handle_workspace(text)
        else:
            self._add_message(
                "System",
                f"Unknown command: [bold]{cmd}[/bold]. Type /help for commands.",
            )

    def _handle_mcp(self, text: str) -> None:
        parts = text.split(maxsplit=2)
        sub = parts[1].lower() if len(parts) > 1 else ""

        if sub == "add":
            self._mcp_add()
        elif sub == "list":
            self._mcp_list()
        elif sub == "remove" and len(parts) > 2:
            self._mcp_remove(parts[2])
        else:
            self._add_message(
                "System",
                "Usage:\n"
                "  /mcp add             — add a new MCP server\n"
                "  /mcp list            — list configured MCP servers\n"
                "  /mcp remove <name>   — remove an MCP server",
            )

    @work(exclusive=False)
    async def _mcp_add(self) -> None:
        result = await self.push_screen_wait(MCPSetupScreen())
        if result is None:
            return
        try:
            self.mcp_manager.add_server(
                name=result["name"],
                command=result.get("command", ""),
                args=result.get("args", []),
                env=result.get("env"),
                transport=result.get("transport", "stdio"),
                url=result.get("url", ""),
            )
            self._add_message(
                "System", f"MCP server [bold]{result['name']}[/bold] added."
            )
        except ValueError as e:
            self._add_message("System", f"[red]{e}[/red]")

    def _mcp_list(self) -> None:
        servers = self.mcp_manager.list_servers()
        if not servers:
            self._add_message("System", "No MCP servers configured.")
            return
        lines = []
        for s in servers:
            if s.transport == "http":
                lines.append(f"  [bold]{s.name}[/bold] — [green]http[/green] {s.url}")
            else:
                lines.append(
                    f"  [bold]{s.name}[/bold] — [green]stdio[/green] {s.command} {' '.join(s.args)}"
                )
            if s.env:
                lines.append(f"    env: {', '.join(s.env.keys())}=")
        self._add_message("System", "Configured MCP servers:\n" + "\n".join(lines))

    def _mcp_remove(self, name: str) -> None:
        try:
            self.mcp_manager.remove_server(name)
            self._add_message("System", f"MCP server [bold]{name}[/bold] removed.")
        except ValueError as e:
            self._add_message("System", f"[red]{e}[/red]")

    def _handle_sessions(self, text: str) -> None:
        parts = text.split(maxsplit=2)
        sub = parts[1].lower() if len(parts) > 1 else ""

        if not sub or sub == "list":
            self._show_sessions_screen()
        elif sub == "switch" and len(parts) > 2:
            self._sessions_switch(parts[2])
        elif sub == "delete" and len(parts) > 2:
            self._sessions_delete(parts[2])
        elif sub == "rename" and len(parts) > 2:
            rest = parts[2].split(maxsplit=1)
            if len(rest) == 2:
                self._sessions_rename(rest[0], rest[1])
            else:
                self._sessions_usage()
        elif sub == "new":
            self._sessions_new()
        elif sub == "purge":
            server_name = parts[2].strip() if len(parts) > 2 else None
            self._sessions_purge(server_name)
        else:
            self._sessions_usage()

    def _sessions_new(self) -> None:
        if self.agent.generating:
            return
        self._save_session()
        self.current_session_id = self.store.create_session(
            server=self._server_name(),
            model=self.backend.model or "",
            profile=self.agent.profile_name or "",
            system_prompt=self.agent.system_prompt or "",
        )
        self.messages.clear()
        if self.agent.system_prompt:
            self.messages.append(
                Message(role="system", content=self.agent.system_prompt)
            )
        self.query_one("#chat-log").remove_children()
        self._add_message("System", "New session created.")

    def _sessions_usage(self) -> None:
        self._add_message(
            "System",
            "Usage:\n"
            "  /sessions                      — browse sessions interactively\n"
            "  /sessions new                  — create a new session\n"
            "  /sessions list                 — list saved sessions\n"
            "  /sessions switch <id>          — switch to another session\n"
            "  /sessions rename <id> <name>   — rename a session\n"
            "  /sessions delete <id>          — delete a session\n"
            "  /sessions purge [server]       — delete ALL sessions (optionally for a server)",
        )

    def _match_session_id(self, prefix: str) -> str | None:
        server = self._server_name()
        sessions = self.store.list_sessions(server)
        matches = [s["id"] for s in sessions if s["id"].startswith(prefix)]
        if len(matches) == 0:
            self._add_message(
                "System",
                f"[red]No session matching '{prefix}'.[/red]",
            )
            return None
        if len(matches) > 1:
            self._add_message(
                "System",
                f"[red]Multiple sessions match '{prefix}': {', '.join(m[:8] for m in matches)}[/red]",
            )
            return None
        return matches[0]

    def _sessions_list(self) -> None:
        server = self._server_name()
        sessions = self.store.list_sessions(server)
        if not sessions:
            self._add_message(
                "System", f"No saved sessions for server [bold]{server}[/bold]."
            )
            return
        lines = []
        for s in sessions:
            marker = (
                " [bright_green]*[/bright_green]"
                if s["id"] == self.current_session_id
                else ""
            )
            short_id = s["id"][:8]
            lines.append(
                f"  {marker}[bold]{short_id}[/bold] — {s['name']} "
                f"({s['msg_count']} msgs, updated {s['updated_at'][:16].replace('T', ' ')})"
            )
        self._add_message(
            "System", f"Sessions for [bold]{server}[/bold]:\n" + "\n".join(lines)
        )

    @work(exclusive=False)
    async def _show_sessions_screen(self) -> None:
        server = self._server_name()
        sessions = self.store.list_sessions(server)
        if not sessions:
            self._add_message(
                "System", f"No saved sessions for server [bold]{server}[/bold]."
            )
            return
        result = await self.push_screen_wait(
            SessionListScreen(sessions, self.current_session_id)
        )
        if result is None:
            return
        action = result[0]
        if action == "switch":
            if self.agent.generating:
                return
            self._sessions_switch(result[1])
        elif action == "delete":
            if self.agent.generating:
                return
            self._sessions_delete(result[1])
        elif action == "rename":
            self._sessions_rename(result[1], result[2])

    def _sessions_switch(self, target: str) -> None:
        if self.agent.generating:
            return
        session_id = self._match_session_id(target)
        if session_id is None:
            return
        if session_id == self.current_session_id:
            self._add_message("System", "Already in this session.")
            return
        self._save_session()
        server = self._server_name()
        data = self.store.load_session(server, session_id)
        if data is None:
            self._add_message(
                "System",
                f"[red]Session {session_id[:8]} not found.[/red]",
            )
            return
        self.messages = sanitize_tool_history(
            [_message_from_dict(m) for m in data.get("messages", [])]
        )
        self.current_session_id = session_id
        self.query_one("#chat-log").remove_children()
        self._add_message("System", f"Switched to session [bold]{data['name']}[/bold].")
        non_system = [m for m in self.messages if m.role != "system"]
        for msg in non_system:
            if msg.role == "user":
                self._add_message("You", msg.content, timestamp=msg.timestamp)
            elif msg.role == "assistant":
                self._add_message("Assistant", msg.content, timestamp=msg.timestamp)

    def _sessions_delete(self, target: str) -> None:
        if self.agent.generating:
            return
        session_id = self._match_session_id(target)
        if session_id is None:
            return
        server = self._server_name()
        if self.store.delete_session(server, session_id):
            self._add_message(
                "System", f"Session [bold]{session_id[:8]}[/bold] deleted."
            )
            if session_id == self.current_session_id:
                self.current_session_id = self.store.create_session(
                    server=server,
                    model=self.backend.model or "",
                    profile=self.agent.profile_name or "",
                    system_prompt=self.agent.system_prompt or "",
                )
                self.messages.clear()
                if self.agent.system_prompt:
                    self.messages.append(
                        Message(role="system", content=self.agent.system_prompt)
                    )
                self.query_one("#chat-log").remove_children()
                self._add_message(
                    "System", "Current session was deleted. New session created."
                )
        else:
            self._add_message(
                "System",
                f"[red]Session {session_id[:8]} not found.[/red]",
            )

    def _sessions_rename(self, target: str, name: str) -> None:
        session_id = self._match_session_id(target)
        if session_id is None:
            return
        server = self._server_name()
        if self.store.rename_session(server, session_id, name):
            self._add_message(
                "System",
                f"Session [bold]{session_id[:8]}[/bold] renamed to [bold]{name}[/bold].",
            )
        else:
            self._add_message(
                "System",
                f"[red]Session {session_id[:8]} not found.[/red]",
            )

    @work(exclusive=False)
    async def _sessions_purge(self, server: str | None = None) -> None:
        if self.agent.generating:
            return
        if server is not None:
            title = f"Delete ALL sessions for server '{server}'?"
            message = f"This will permanently delete every saved session for [bold]{server}[/bold].\nThis cannot be undone."
            store_count = lambda: self.store.purge_server_sessions(server)
        else:
            title = "Delete ALL sessions?"
            message = "This will permanently delete every saved session.\nThis cannot be undone."
            store_count = lambda: self.store.purge_all_sessions()
        result = await self.push_screen_wait(ConfirmScreen(title, message))
        if result != "yes":
            self._add_message("System", "Purge cancelled.")
            return
        count = store_count()
        self.messages.clear()
        if self.agent.system_prompt:
            self.messages.append(
                Message(role="system", content=self.agent.system_prompt)
            )
        server = self._server_name()
        self.current_session_id = self.store.create_session(
            server=server,
            model=self.backend.model or "",
            profile=self.agent.profile_name or "",
            system_prompt=self.agent.system_prompt or "",
        )
        self.query_one("#chat-log").remove_children()
        self._add_message(
            "System",
            f"Purge complete. [bold]{count}[/bold] session(s) deleted. New session created.",
        )

    def _handle_workspace(self, text: str) -> None:
        parts = text.split(maxsplit=2)
        sub = parts[1].lower() if len(parts) > 1 else ""

        if sub == "list":
            self._workspace_list()
        elif sub == "set" and len(parts) > 2:
            self._workspace_set(parts[2])
        elif sub == "unset":
            self._workspace_unset()
        else:
            self._add_message(
                "System",
                "Usage:\n"
                "  /workspace list              — list workspaces\n"
                "  /workspace set <path>        — set workspace directory\n"
                "  /workspace unset             — clear workspace",
            )

    @work(exclusive=False)
    async def _workspace_list(self) -> None:
        workspaces = self.workspace_manager.list_workspaces()
        result = await self.push_screen_wait(
            WorkspaceListScreen(self.session.workspace, workspaces)
        )
        if result:
            await self._do_workspace_set(result)

    async def _do_workspace_set(self, path_str: str) -> None:
        path_str = path_str.strip()
        path = Path(path_str).expanduser().resolve()
        if not path.is_dir():
            self._add_message(
                "System",
                f"[red]Not a valid directory: {path}[/red]",
            )
            return

        if (
            is_sensitive_path(path)
            and SCOPE_WORKSPACE_SENSITIVE not in self.session._session_grants
        ):
            decision = await self.push_screen_wait(
                PermissionScreen(f"Set workspace to potentially sensitive path: {path}")
            )
            if decision == "deny":
                self._add_message(
                    "System",
                    f"Workspace set denied for sensitive path: [bold red]{path}[/bold red]",
                )
                return
            grant_session = decision == "session"
        else:
            grant_session = False

        self.session.workspace = path
        self.session._session_grants.clear()
        if grant_session:
            self.session._session_grants.add(SCOPE_WORKSPACE_SENSITIVE)
        self.workspace_manager.add_workspace(path)
        self._add_message(
            "System",
            f"Workspace set to: [bold green]{path}[/bold green]",
        )

    @work(exclusive=False)
    async def _workspace_set(self, path_str: str) -> None:
        await self._do_workspace_set(path_str)

    def _workspace_unset(self) -> None:
        self.session.workspace = None
        self.session._session_grants.clear()
        self._add_message(
            "System",
            "Workspace cleared. Read tools will always require permission.",
        )

    def _handle_context(self) -> None:
        active = self.server_manager.get_active()
        backend_label = f"[bold green]{self.config.backend}[/bold green]"
        if self.config.backend == "ollama":
            server_url = active.url if active else self.backend.base_url
            server_name = f" ({active.name})" if active else ""
            server_line = f"Server: {server_url}{server_name}\n"
        elif self.config.backend == "openai":
            server_line = (
                f"API: [bold green]{self.config.openai_base_url}[/bold green]\n"
            )
        elif self.config.backend == "huggingface":
            server_line = (
                f"API: [bold green]{self.config.huggingface_base_url}[/bold green]\n"
            )
        elif self.config.backend == "transformers":
            server_line = (
                f"Local: [bold green]{self.config.transformers_device}[/bold green]\n"
            )
        else:
            server_line = ""
        current_model_label = (
            f"[bold green]{self.backend.model}[/bold green]"
            if self.backend.model
            else "[bold bright_green]None[/bold bright_green]"
        )
        large_model = self._get_large_model() or "—"
        small_model = self._get_small_model() or "—"
        profile = self.agent.profile_name if self.agent.profile_name else "none"
        profile_version = ""
        if self.agent.profile_data is not None:
            m = self.agent.profile_data.manifest
            profile_version = f" v{m.version}"
            if m.base:
                profile_version += f" (base: {m.base})"
        ws_label = (
            f"[bold green]{self.session.workspace}[/bold green]"
            if self.session.workspace
            else "[red]Not set[/red]"
        )
        self._add_message(
            "System",
            f"Backend: {backend_label}\n"
            f"{server_line}"
            f"Current model: {current_model_label} [dim]({self.model_size})[/dim]\n"
            f"Large: [bold green]{large_model}[/bold green]  ·  Small: [bold green]{small_model}[/bold green]\n"
            f"Status: {self._status_badges()}\n"
            f"Profile: [bold green]{profile}[/bold green]{profile_version}\n"
            f"Workspace: {ws_label}",
        )

    def _handle_offline(self) -> None:
        self.config.offline_mode = not self.config.offline_mode
        self.mcp_manager._offline_mode = self.config.offline_mode
        status = "enabled" if self.config.offline_mode else "disabled"
        self._add_message("System", f"Offline mode [bold]{status}[/bold].")
        self.update_header()

    def _handle_dry_run(self) -> None:
        self.config.dry_run = not self.config.dry_run
        status = "enabled" if self.config.dry_run else "disabled"
        self._add_message("System", f"Dry-run mode [bold]{status}[/bold].")
        self.update_header()

    def _handle_mode(self, parts: list[str]) -> None:
        if len(parts) < 2 or parts[1].lower() not in ("ask", "agent", "chat", "plan"):
            self._add_message(
                "System",
                "Usage: [bold]/mode [ask|agent|chat|plan][/bold]\n"
                "  [bold]ask[/bold]   — read-only tools enabled\n"
                "  [bold]agent[/bold] — all tools enabled\n"
                "  [bold]chat[/bold]  — no tools, simple chat\n"
                "  [bold]plan[/bold]  — research + notebook/todowrite tools, saves a plan to notes/plan-<name>.md",
            )
            return
        mode = parts[1].lower()
        before = len(self.messages)
        cleaned = sanitize_tool_history(self.messages)
        dropped = before - len(cleaned)
        if dropped:
            self.messages[:] = cleaned
            self.notify(
                f"Reconciled {dropped} stale tool message(s) for mode switch.",
                severity="information",
                timeout=4,
            )
        self.agent.set_mode(mode)
        self.update_header()
        self._add_message("System", f"Switched to [bold]{mode}[/bold] mode.")

    def _handle_profile(self, text: str) -> None:
        parts = text.split(maxsplit=2)
        sub = parts[1].lower() if len(parts) > 1 else ""

        if sub == "list":
            profiles = self.agent.list_profiles()
            if not profiles:
                self._add_message("System", "No profiles found in profiles/ directory.")
                return
            lines = [f"  [bold]{p}[/bold]" for p in profiles]
            self._add_message("System", "Available profiles:\n" + "\n".join(lines))
        elif sub == "load" and len(parts) > 2:
            self._profile_load(parts[2])
        elif sub == "create" and len(parts) > 2:
            self._profile_create(parts[2])
        else:
            self._add_message(
                "System",
                "Usage:\n"
                "  /profile list            — list available profiles\n"
                "  /profile load <name>     — load a profile (clears conversation)\n"
                "  /profile create <name>   — generate a new profile with the current model",
            )

    @work(exclusive=False)
    async def _profile_load(self, name: str) -> None:
        if self.agent.generating:
            return
        try:
            self.agent.load_profile(name)
        except ValueError as e:
            self._add_message("System", f"[red]{e}[/red]")
            return
        if self.agent.permission_enforcer is not None:
            self._builtin_tools._permission_enforcer = self.agent.permission_enforcer
        self._save_session()
        self.messages.clear()
        self.query_one("#chat-log").remove_children()
        self.messages.append(Message(role="system", content=self.agent.system_prompt))
        self.update_header()
        self._save_session()
        self._add_message("System", f"Loaded profile: [bold]{name}[/bold]")

    @work(exclusive=False)
    async def _profile_create(self, name: str) -> None:
        if self.agent.generating:
            return

        if not re.match(r"^[a-zA-Z0-9_-]+$", name):
            self._add_message(
                "System",
                "[red]Invalid profile name. Use letters, numbers, hyphens, and underscores only.[/red]",
            )
            return

        if self.agent.profile_exists(name):
            self._add_message(
                "System",
                f"[red]Profile '{name}' already exists.[/red]",
            )
            return

        if self.backend.model is None:
            self._add_message(
                "System",
                "[red]No model selected. Use /models <name> to pick a model first.[/red]",
            )
            return

        self._add_message(
            "System",
            f"Generating profile [bold]{name}[/bold] with {self.backend.model}...",
        )

        default_data = None
        try:
            default_data = self.agent._profile_manager.load_profile("default")
        except ValueError:
            logger.debug("Failed to load default profile for reference")

        prompt = (
            "Create an AGENTS.md file that will serve as the system prompt for an AI agent.\n\n"
            "The agent has access to these tools:\n"
            "- **read_file**, **write_file**, **edit_file** — file operations\n"
            "- **glob**, **grep**, **list_directory** — file search\n"
            "- **run_command** — shell commands\n"
            "- **websearch**, **fetch** — web access\n"
            "- **think** — internal reasoning scratchpad\n\n"
            f'The profile name is "{name}". '
            "Create a system prompt tailored to an agent specialized for this purpose.\n\n"
            "Use this structure:\n"
            '1. A single opening line: "You are an AI assistant specialized in..."\n'
            "2. A section listing relevant tools\n"
            "3. Behavioral guidelines specific to the role\n"
        )
        if default_data:
            prompt += f"\nHere is the default profile as a reference:\n\n```\n{default_data.system_prompt}\n```\n"
        prompt += "\nOutput only the AGENTS.md content, no extra commentary."

        messages = [Message(role="user", content=prompt)]
        response = await self.backend.generate(model=None, messages=messages)

        if response.finish_reason == "error":
            self._add_message(
                "System",
                f"[red]Failed to generate profile: {response.error}[/red]",
            )
            return

        content = response.content.strip()
        if not content:
            self._add_message("System", "[red]Model returned empty content.[/red]")
            return

        content = re.sub(r"^```(?:markdown)?\n", "", content)
        content = re.sub(r"\n```$", "", content)
        content = content.strip()

        try:
            self.agent.create_profile(name, content)
        except FileExistsError:
            self._add_message(
                "System",
                f"[red]Profile directory '{name}' already exists.[/red]",
            )
            return
        except OSError as e:
            self._add_message("System", f"[red]Failed to create profile: {e}[/red]")
            return

        self._add_message(
            "System",
            f"Profile [bold]{name}[/bold] created. Use [bold]/profile load {name}[/bold] to activate it.",
        )
        self._add_message("Assistant", content)

    def _handle_server(self, text: str) -> None:
        parts = text.split(maxsplit=2)
        sub = parts[1].lower() if len(parts) > 1 else ""

        if sub in ("add", "list", "remove", "switch", "default"):
            if sub == "add" and len(parts) > 2:
                rest = parts[2].split(maxsplit=1)
                if len(rest) < 2:
                    self._add_message(
                        "System", "Usage: [bold]/servers add <name> <url>[/bold]"
                    )
                    return
                name, url = rest[0], rest[1]
                self._server_add(name, url)
            elif sub == "list":
                self._server_list()
            elif sub == "remove" and len(parts) > 2:
                self._server_remove(parts[2])
            elif sub == "switch" and len(parts) > 2:
                self._server_switch(parts[2])
            elif sub == "default" and len(parts) > 2:
                self._server_default(parts[2])
            else:
                self._add_message(
                    "System",
                    "Usage:\n"
                    "  /servers add <name> <url>      — add & validate Ollama server\n"
                    "  /servers list                  — list configured servers\n"
                    "  /servers remove <name>         — remove a server\n"
                    "  /servers default <name>        — set default server\n"
                    "  /servers switch <name>         — switch active server\n"
                    "  /servers <name> use-model <model>  — set default model for a server",
                )
        elif len(parts) > 2:
            rest = parts[2].split(maxsplit=1)
            if len(rest) == 2 and rest[0] == "use-model":
                self._server_set_default_model(parts[1], rest[1])
            else:
                self._add_message(
                    "System",
                    "Usage:\n"
                    "  /servers add <name> <url>      — add & validate Ollama server\n"
                    "  /servers list                  — list configured servers\n"
                    "  /servers remove <name>         — remove a server\n"
                    "  /servers default <name>        — set default server\n"
                    "  /servers switch <name>         — switch active server\n"
                    "  /servers <name> use-model <model>  — set default model for a server",
                )
        else:
            self._add_message(
                "System",
                "Usage:\n"
                "  /servers add <name> <url>      — add & validate Ollama server\n"
                "  /servers list                  — list configured servers\n"
                "  /servers remove <name>         — remove a server\n"
                "  /servers default <name>        — set default server\n"
                "  /servers switch <name>         — switch active server\n"
                "  /servers <name> use-model <model>  — set default model for a server",
            )

    @work(exclusive=False)
    async def _server_add(self, name: str, url: str) -> None:
        if not url.startswith("http://") and not url.startswith("https://"):
            self._add_message(
                "System",
                "[red]URL must start with http:// or https://[/red]",
            )
            return

        # NOTE: this is currently only for Ollama backends.
        # Other upstream servers won't get this check when added.
        # TODO: consider adding a generic ping/healthcheck for other backends if they support it.
        if self.config.backend == "ollama":
            ok, err = await ServerManager.validate_ollama_url(url)
            if not ok:
                self._add_message(
                    "System",
                    f"[red]Failed to connect to {url}: {err}[/red]",
                )
                return

        try:
            is_first = self.server_manager.add_server(name, url)
            if is_first:
                self.backend.set_base_url(url)
                self.update_header()
            self._add_message(
                "System", f"Ollama server [bold]{name}[/bold] added ({url})."
            )
        except ValueError as e:
            self._add_message("System", f"[red]{e}[/red]")

    def _server_list(self) -> None:
        servers = self.server_manager.list_servers()
        if not servers:
            self._add_message("System", "No Ollama servers configured.")
            return
        self.push_screen(ServerListScreen(servers), self._server_list_selected)

    def _server_list_selected(self, name: Optional[str]) -> None:
        if name:
            self._server_switch(name)

    def _server_remove(self, name: str) -> None:
        try:
            removed = self.server_manager.remove_server(name)
            if removed.active:
                active = self.server_manager.get_active()
                if active:
                    self.backend.set_base_url(active.url)
                self.update_header()
            self._add_message("System", f"Ollama server [bold]{name}[/bold] removed.")
        except ValueError as e:
            self._add_message("System", f"[red]{e}[/red]")

    def _server_default(self, name: str) -> None:
        try:
            config = self.server_manager.switch_server(name)
            self.backend.set_base_url(config.url)
            model_to_use = config.large_model or config.default_model
            if model_to_use:
                self._switch_model(model_to_use)
            self.update_header()
            self._add_message(
                "System", f"Default server set to: [bold]{name}[/bold] ({config.url})"
            )
        except ValueError as e:
            self._add_message("System", f"[red]{e}[/red]")

    def _server_set_default_model(self, name: str, model: str) -> None:
        try:
            self.server_manager.set_default_model(name, model)
            active = self.server_manager.get_active()
            if active and active.name == name:
                self._switch_model(model)
            if self.config.backend == "ollama":
                self._persist_model_to_settings(large=model)
            self._add_message(
                "System",
                f"Default model for [bold]{name}[/bold] set to: [bold]{model}[/bold]",
            )
        except ValueError as e:
            self._add_message("System", f"[red]{e}[/red]")

    def _server_switch(self, name: str) -> None:
        try:
            self._save_session()
            config = self.server_manager.switch_server(name)
            self.backend.set_base_url(config.url)
            model_to_use = config.large_model or config.default_model
            if model_to_use:
                self._switch_model(model_to_use)
            self.messages.clear()
            self.query_one("#chat-log").remove_children()
            self.current_session_id = self.store.create_session(
                server=name,
                model=self.backend.model or "",
                profile=self.agent.profile_name or "",
                system_prompt=self.agent.system_prompt or "",
            )
            if self.agent.system_prompt:
                self.messages.append(
                    Message(role="system", content=self.agent.system_prompt)
                )
            self.update_header()
            self._add_message(
                "System", f"Switched to server: [bold]{name}[/bold] ({config.url})"
            )
        except ValueError as e:
            self._add_message("System", f"[red]{e}[/red]")

    @work(exclusive=True)
    async def _show_model_picker(self) -> None:
        try:
            # Ollama's client has a list() method to retrieve available models, but other backends may not.
            if hasattr(self.backend.client, "list") and callable(
                getattr(self.backend.client, "list")
            ):
                response = await self.backend.client.list()
                names = [m.model for m in response.models if m.model]
                if self.config.model_filters:
                    exclude = [
                        f.strip()
                        for f in self.config.model_filters.split(",")
                        if f.strip()
                    ]
                    if exclude:
                        names = [n for n in names if not any(ex in n for ex in exclude)]
                if not names:
                    self._add_message(
                        "System",
                        "No models found for this backend.",
                    )
                    return
                picker = ModelPicker(names, current=self.backend.model)
                model = await self.push_screen_wait(picker)
                if model:
                    self._switch_model(model)
            else:
                self._add_message(
                    "System",
                    "Model listing is not supported for this backend. "
                    "Set manually with /model set-small <name> and /model set-large <name>. See /help for other commands",
                )
        except Exception as e:
            self._add_message("System", f"Error listing models: [red]{e}[/red]")

    def _switch_model(self, name: str) -> None:
        self.backend.model = name
        self.update_header()
        self._add_message("System", f"Switched to model: [bold]{name}[/bold]")

    def _get_large_model(self) -> str:
        if self.config.backend == "openai":
            return self.config.openai_model
        if self.config.backend == "huggingface":
            return self.config.huggingface_model
        if self.config.backend == "transformers":
            return self.config.transformers_model
        active = self.server_manager.get_active()
        if active and active.large_model:
            return active.large_model
        return self.config.ollama_model

    def _get_small_model(self) -> str:
        if self.config.backend == "openai":
            return self.config.openai_small_model
        if self.config.backend == "huggingface":
            return self.config.huggingface_small_model
        if self.config.backend == "transformers":
            return (
                self.config.transformers_small_model or self.config.transformers_model
            )
        active = self.server_manager.get_active()
        if active and active.small_model:
            return active.small_model
        return self.config.ollama_small_model

    def _handle_model_switch(self, text: str) -> None:
        parts = text.split(maxsplit=2)
        sub = parts[1].lower() if len(parts) > 1 else ""

        if sub == "add" and len(parts) > 2:
            self._model_add(parts[2])
        elif sub == "remove" and len(parts) > 2:
            self._model_remove(parts[2])
        elif sub == "set-large" and len(parts) > 2:
            self._model_set_large(parts[2])
        elif sub == "set-small" and len(parts) > 2:
            self._model_set_small(parts[2])
        elif sub == "list":
            self._model_list()
        elif sub in ("large", "small"):
            model = (
                self._get_large_model() if sub == "large" else self._get_small_model()
            )
            if not model:
                self._add_message(
                    "System",
                    f"[red]No {sub} model configured. "
                    f"Use /model set-{sub} <name> to set one.[/red]",
                )
                return
            self.model_size = sub
            self._builtin_tools.model_tier = self.model_size
            self._switch_model(model)
        else:
            self._add_message(
                "System",
                "Usage:\n"
                "  /model add <name> [--large|--small|--default]  — register a model\n"
                "  /model remove <name>                 — unregister a model\n"
                "  /model list                          — list registered models\n"
                "  /model large|small                   — switch between large/small model\n"
                "  /model set-large <model-name>        — set per-server large model\n"
                "  /model set-small <model-name>        — set per-server small model",
            )

    def _model_set_large(self, model: str) -> None:
        active = self.server_manager.get_active()
        if not active:
            self._add_message(
                "System",
                "[red]No active server to set model for.[/red]",
            )
            return
        self.server_manager.set_large_model(active.name, model)
        self._persist_model_to_settings(large=model)
        self._switch_model(model)
        self.model_size = "large"
        self._builtin_tools.model_tier = self.model_size

    def _model_set_small(self, model: str) -> None:
        active = self.server_manager.get_active()
        if not active:
            self._add_message(
                "System",
                "[red]No active server to set model for.[/red]",
            )
            return
        self.server_manager.set_small_model(active.name, model)
        self._persist_model_to_settings(small=model)
        self._switch_model(model)
        self.model_size = "small"
        self._builtin_tools.model_tier = self.model_size

    def _persist_model_to_settings(self, large: str = "", small: str = "") -> None:
        backend = self.config.backend
        section = self.settings.setdefault(backend, {})
        if backend == "transformers":
            if large:
                section["model"] = large
                self.config.transformers_model = large
            if small:
                section["small_model"] = small
                self.config.transformers_small_model = small
        else:
            if large:
                section["large_model"] = large
            if small:
                section["small_model"] = small
            if backend == "openai":
                if large:
                    self.config.openai_model = large
                if small:
                    self.config.openai_small_model = small
            elif backend == "huggingface":
                if large:
                    self.config.huggingface_model = large
                if small:
                    self.config.huggingface_small_model = small
            elif backend == "ollama":
                if large:
                    self.config.ollama_model = large
                if small:
                    self.config.ollama_small_model = small
        self._add_message(
            "System", "Model setting saved to [bold]~/.config/oli/settings.json[/bold]."
        )
        self.settings_manager.save(self.settings)

    def _model_add(self, args: str) -> None:
        """Register a new model for the active server.

        Usage: /model add <name> [--large|--small|--default]
        Example: /model add gpt4-turbo --large
        """
        active = self.server_manager.get_active()
        if not active:
            self._add_message(
                "System",
                "[red]No active server to register model for.[/red]",
            )
            return

        # Parse arguments: model_name [--tier]
        parts = args.split()
        if not parts:
            self._add_message(
                "System",
                "Usage: [bold]/model add <name> [--large|--small|--default][/bold]",
            )
            return

        model_name = parts[0]
        tier = None

        # Parse optional tier flag
        if len(parts) > 1:
            tier_arg = parts[1].lower()
            if tier_arg in ("--large", "--small", "--default"):
                tier = tier_arg[2:]  # Remove '--' prefix
            else:
                self._add_message(
                    "System",
                    "[red]Invalid flag. Use --large, --small, or --default.[/red]",
                )
                return

        try:
            self.server_manager.add_model(active.name, model_name, model_name, tier)
            if tier:
                self._add_message(
                    "System",
                    f"Model [bold]{model_name}[/bold] registered as [bold]{tier}[/bold] model.",
                )
            else:
                self._add_message(
                    "System",
                    f"Model [bold]{model_name}[/bold] registered.",
                )
        except ValueError as e:
            self._add_message("System", f"[red]{e}[/red]")

    def _model_remove(self, model_name: str) -> None:
        """Remove a registered model from the active server.

        Usage: /model remove <name>
        Example: /model remove gpt4-turbo
        """
        active = self.server_manager.get_active()
        if not active:
            self._add_message(
                "System",
                "[red]No active server to remove model from.[/red]",
            )
            return

        if not model_name:
            self._add_message(
                "System",
                "Usage: [bold]/model remove <name>[/bold]",
            )
            return

        try:
            removed_model = self.server_manager.remove_model(active.name, model_name)
            self._add_message(
                "System",
                f"Model [bold]{model_name}[/bold] (mapped to [bold]{removed_model}[/bold]) removed.",
            )
        except ValueError as e:
            self._add_message("System", f"[red]{e}[/red]")

    def _model_list(self) -> None:
        """List all registered models for the active server."""
        active = self.server_manager.get_active()
        if not active:
            self._add_message(
                "System",
                "[red]No active server.[/red]",
            )
            return

        try:
            models = self.server_manager.list_models(active.name)
            if not models:
                self._add_message(
                    "System",
                    f"No registered models for server [bold]{active.name}[/bold].",
                )
                return

            lines = []
            for friendly_name, actual_model in sorted(models.items()):
                tier_hint = ""
                if actual_model == active.large_model:
                    tier_hint = " [green]→ large[/green]"
                elif actual_model == active.small_model:
                    tier_hint = " [green]→ small[/green]"
                elif actual_model == active.default_model:
                    tier_hint = " [green]→ default[/green]"
                lines.append(
                    f"  [bold]{friendly_name}[/bold] = {actual_model}{tier_hint}"
                )

            self._add_message(
                "System",
                f"Registered models for [bold]{active.name}[/bold]:\n"
                + "\n".join(lines),
            )
        except ValueError as e:
            self._add_message("System", f"[red]{e}[/red]")

    def _sync_settings_from_runtime(self) -> None:
        s = self.settings
        s["openai"]["large_model"] = self.config.openai_model
        s["openai"]["small_model"] = self.config.openai_small_model
        s["openai"]["vision_style"] = self.config.openai_vision_style
        s["ollama"]["large_model"] = self.config.ollama_model
        s["ollama"]["small_model"] = self.config.ollama_small_model
        s["huggingface"]["large_model"] = self.config.huggingface_model
        s["huggingface"]["small_model"] = self.config.huggingface_small_model
        s["huggingface"]["remote"] = self.config.huggingface_remote
        s["transformers"]["model"] = self.config.transformers_model
        s["transformers"]["small_model"] = self.config.transformers_small_model
        s["transformers"]["is_multi_model"] = self.config.transformers_is_multi_model
        mp = s.setdefault("model_params", {})
        mp["use_agent_pool"] = self.config.use_agent_pool
        mp["agent_pool_size"] = self.config.agent_pool_size
        lg = s.setdefault("logging", {})
        lg["log_level"] = self.config.log_level
        lg["log_file"] = self.config.log_file
        api = s.setdefault("api_server", {})
        api["host"] = self.config.api_host
        api["port"] = self.config.api_port
        api["profile"] = self.config.api_profile
        api["mode"] = self.config.api_mode
        paths = s.setdefault("paths", {})
        paths["profiles_dir"] = self.config.profiles_dir
        paths["logs_dir"] = self.config.logs_dir
        backend = self.config.backend
        section = s.setdefault(backend, {})
        eff_large = self._get_large_model()
        eff_small = self._get_small_model()
        if backend == "transformers":
            if eff_large:
                section["model"] = eff_large
            section["small_model"] = eff_small
        else:
            section["large_model"] = eff_large
            section["small_model"] = eff_small

    @work(exclusive=False)
    async def _handle_config(self) -> None:
        self._sync_settings_from_runtime()
        result = await self.push_screen_wait(ConfigScreen(self.settings))
        if result is None:
            return
        self.settings_manager.save(result)
        self.settings = result
        old_backend = self.config.backend
        self.config = self.settings_manager.to_appconfig(result)
        self.agent.config = self.config
        self._builtin_tools._config = self.config
        self.mcp_manager._offline_mode = self.config.offline_mode

        # Always rebuild: base_url / api_key / vision_style changes must
        # take effect even when the backend TYPE didn't change.
        type_changed = result["backend"] != old_backend
        self._rebuild_backend(announce=type_changed)

        self.update_header()
        self._add_message(
            "System", "Settings saved to [bold]~/.config/oli/settings.json[/bold]."
        )

    def _rebuild_backend(self, announce: bool = True) -> None:
        active = self.server_manager.get_active()
        url = active.url if active else self.config.ollama_base_url
        if self.config.backend == "openai":
            url = self.config.openai_base_url
            self.backend = create_model_backend(
                url,
                "openai",
                api_key=self.config.openai_api_key,
                base_url=self.config.openai_base_url,
            )
        elif self.config.backend == "huggingface":
            url = self.config.huggingface_base_url
            self.backend = create_model_backend(
                url,
                "huggingface",
                api_key=self.config.huggingface_api_key,
                base_url=self.config.huggingface_base_url,
            )
        elif self.config.backend == "transformers":
            self.backend = create_model_backend(
                url,
                "transformers",
                model=self.config.transformers_model,
            )
        else:
            self.backend = create_model_backend(url, self.config.backend.lower())
        self.agent.backend = self.backend
        self._builtin_tools._backend = self.backend
        model = (
            self._get_large_model()
            if self.model_size == "large"
            else self._get_small_model()
        )
        if model:
            self.backend.model = model
        if announce:
            self._add_message(
                "System",
                f"Switched to [bold]{self.config.backend}[/bold] backend.",
            )

    def action_clear_chat(self) -> None:
        if self.agent.generating:
            return
        self.messages.clear()
        if self.agent.system_prompt:
            self.messages.append(
                Message(role="system", content=self.agent.system_prompt)
            )
        self.query_one("#chat-log").remove_children()
        self._add_message("System", "Conversation cleared.")
        self._save_session()

    def action_go_home(self) -> None:
        if self.agent.generating:
            return
        self._save_session()
        self.messages.clear()
        chat_log = self.query_one("#chat-log")
        try:
            welcome = chat_log.query_one("#welcome")
            welcome.update(self._render_welcome_panel())
        except Exception:
            chat_log.remove_children()
            chat_log.mount(self._render_welcome())

    def action_copy_last_message(self) -> None:
        for widget in reversed(list(self.query(".message"))):
            plain_text = getattr(widget, "plain_text", None)
            if plain_text:
                try:
                    clean = RichText.from_markup(plain_text).plain
                except Exception:
                    clean = plain_text
                self.copy_to_clipboard(clean)
                self.notify("Copied to clipboard", timeout=2)
                return
        self.notify("No message to copy", severity="warning", timeout=2)

    # --- Message handling ---

    def _role_color(self, role: str) -> str:
        for base, color in self.ROLE_COLORS.items():
            if role == base or role.startswith(base + " "):
                return color
        return "green"

    def _role_icon(self, role: str) -> str:
        for base, icon in self.ROLE_ICONS.items():
            if role == base or role.startswith(base + " "):
                return icon
        return "\u25cf"

    def _flat(self, content, header_markup: str) -> Group:
        """Flat (unboxed) message rendering: a label line + indented body."""
        return Group(
            RichText.from_markup(header_markup), Padding(content, (0, 0, 0, 2))
        )

    def _status_badges(self) -> str:
        badges: list[str] = [
            f"[bold {PRIMARY_HEX}]\\[{self.agent.mode.upper()}][/bold {PRIMARY_HEX}]"
        ]
        badges.append(f"[dim]\\[{self.model_size.upper()}][/dim]")
        if self.config.offline_mode:
            badges.append("[bold blue]\\[OFFLINE][/bold blue]")
        if self.config.dry_run:
            badges.append("[bold red]\\[DRY-RUN][/bold red]")
        return " ".join(badges)

    def _handle_user_message(self, text: str) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        self.messages.append(Message(role="user", content=text, timestamp=ts))
        chat_log = self.query_one("#chat-log")
        color = self._role_color("You")
        icon = self._role_icon("You")
        title = f"[{color}]{icon} You[/{color}]"
        formatted_ts = self._format_timestamp(ts)
        if formatted_ts:
            title += f" [dim]· {formatted_ts}[/dim]"
        static = Static(self._flat(Markdown(text), title), classes="message")
        static.plain_text = text
        chat_log.mount(static)
        chat_log.scroll_end(animate=False)
        self._generate_response()

    @work(exclusive=True)
    async def _generate_response(self) -> None:
        input_widget = self.query_one("#chat-input")
        input_widget.disabled = True

        if self.backend.model is None:
            self._add_message(
                "System",
                "[red]No model selected. Use /models <name> to pick a model.[/red]",
            )
            input_widget.disabled = False
            return

        chat_log = self.query_one("#chat-log")

        if self.agent.mode == "agent":
            tools = await self.mcp_manager.get_available_tools()
            for w in self.mcp_manager.pop_warnings():
                self.notify(w, severity="warning", timeout=5)
        elif self.agent.mode == "ask":
            tools = await self.mcp_manager.get_readonly_tools()
            for w in self.mcp_manager.pop_warnings():
                self.notify(w, severity="warning", timeout=5)
        elif self.agent.mode == "plan":
            tools = await self.mcp_manager.get_plan_tools()
            for w in self.mcp_manager.pop_warnings():
                self.notify(w, severity="warning", timeout=5)
        else:
            tools = None

        # --- restartable spinner ---
        SPINNER_CHARS = ("o o", "- o", "- -", "o -")  # blinking owl eyes
        ICON_SUCCESS = "\\[^_^]"
        ICON_ERROR = "\\[x_x]"
        ICON_EMPTY = "\\[-_-]"
        ICON_THINK = "\\[o.o]"
        ICON_ESCUP = ">>"

        _spinner = {"widget": None, "timer": None, "idx": 0}

        async def start_spinner() -> None:
            if _spinner["timer"] is not None:
                return
            widget = Static(f"[dim]\\[{SPINNER_CHARS[0]}][/dim]", classes="message")
            await chat_log.mount(widget)
            chat_log.scroll_end(animate=False)

            def spin():
                _spinner["idx"] = (_spinner["idx"] + 1) % len(SPINNER_CHARS)
                try:
                    widget.update(f"[dim]\\[{SPINNER_CHARS[_spinner['idx']]}][/dim]")
                except Exception:
                    logger.debug("Spinner update failed (widget likely gone)")

            _spinner["widget"] = widget
            _spinner["timer"] = self.set_interval(0.3, spin)

            _spinner["widget"] = widget
            _spinner["timer"] = self.set_interval(0.3, spin)

        def stop_spinner() -> None:
            t = _spinner["timer"]
            if t is not None:
                t.stop()
                _spinner["timer"] = None
            w = _spinner["widget"]
            if w is not None:
                try:
                    w.remove()
                except Exception:
                    logger.debug("Spinner remove failed (widget likely gone)")
                _spinner["widget"] = None

        msg_widget: Static | None = None
        full_response = ""
        assistant_ts: str | None = None
        think_widget: Collapsible | None = None
        think_inner: Static | None = None
        think_text = ""

        await start_spinner()
        try:
            async for event in self.agent.process(
                self.messages, tools=tools, confirm_callback=self._permission_callback
            ):
                match event:
                    case ToolCallExecuting(name, parameters):
                        stop_spinner()
                        if parameters:
                            params = ", ".join(
                                f"{k}={v}" for k, v in parameters.items()
                            )
                            if len(params) > 100:
                                params = params[:100] + "…"
                            base = f"[bold]{name}[/bold]  [dim]· {params}[/dim]"
                        else:
                            base = f"[bold]{name}[/bold]"
                        content = f"[dim]◐[/dim] {base}"
                        widget = Static(content, classes="message")
                        widget.plain_text = content
                        widget._tool_base = base
                        widget._tool_start = time.monotonic()
                        await chat_log.mount(widget)
                        chat_log.scroll_end(animate=False)
                        self._last_tool_widget = widget
                    case ToolCallResult(name, result):
                        await start_spinner()
                        widget = self._last_tool_widget
                        if widget is not None:
                            is_error = str(result).lstrip().startswith("Error")
                            icon = "[red]✗[/red]" if is_error else "[green]✓[/green]"
                            start = getattr(widget, "_tool_start", None)
                            elapsed = (
                                f"  [dim]· {time.monotonic() - start:.1f}s[/dim]"
                                if start is not None
                                else ""
                            )
                            base = getattr(widget, "_tool_base", f"[bold]{name}[/bold]")
                            updated = f"{icon} {base}{elapsed}"
                            if self.config.log_level == "DEBUG":
                                result_text = str(result)[:2000]
                                updated += f"\n  [dim]{result_text}[/dim]"
                            widget.update(updated)
                            widget.plain_text = updated
                    case AssistantResponse(content):
                        stop_spinner()
                        if think_widget is not None:
                            think_widget.title = "💭 Thinking"
                        if msg_widget is None:
                            ts = datetime.now(timezone.utc).isoformat()
                            self._add_message(
                                f"Assistant {ICON_SUCCESS}", content, timestamp=ts
                            )
                        # This iteration's text was already streamed into msg_widget;
                        # reset per-iteration state so the next round mounts fresh
                        # widgets at the current bottom instead of reusing this one.
                        msg_widget = None
                        full_response = ""
                        assistant_ts = None
                        think_widget = None
                        think_inner = None
                        think_text = ""
                    case ThinkingChunk(text=thinking_text):
                        stop_spinner()
                        if think_widget is None:
                            think_inner = Static(classes="message")
                            think_widget = Collapsible(
                                think_inner,
                                title="💭 Thinking...",
                                collapsed=False,
                                classes="message",
                            )
                            await chat_log.mount(think_widget)
                            chat_log.scroll_end(animate=False)
                        think_text += thinking_text
                        try:
                            try:
                                rendered_think = Markdown(think_text)
                            except Exception:
                                rendered_think = think_text
                            think_inner.update(rendered_think)
                        except Exception:
                            logger.warning("Failed to update thinking panel")
                        chat_log.scroll_end(animate=False)
                    case StreamChunk(text):
                        stop_spinner()
                        if think_widget is not None:
                            think_widget.title = "💭 Thinking"
                            think_widget.collapsed = True
                        if msg_widget is None:
                            msg_widget = Static(classes="message")
                            msg_widget.plain_text = ""
                            await chat_log.mount(msg_widget)
                            chat_log.scroll_end(animate=False)
                            assistant_ts = datetime.now(timezone.utc).isoformat()
                        full_response += text
                        msg_widget.plain_text = full_response
                        try:
                            try:
                                rendered = Markdown(full_response)
                            except Exception:
                                rendered = full_response
                            assistant_color = self._role_color("Assistant")
                            title = (
                                f"[{assistant_color}]{self._role_icon('Assistant')} "
                                f"Assistant {ICON_SUCCESS}[/{assistant_color}]"
                            )
                            ts_display = self._format_timestamp(assistant_ts)
                            if ts_display:
                                title += f" [dim]· {ts_display}[/dim]"
                            msg_widget.update(self._flat(rendered, title))
                        except Exception:
                            logger.warning("Failed to update streaming response panel")
                        chat_log.scroll_end(animate=False)
                    case AgentError(message):
                        stop_spinner()
                        self.notify(message, severity="error")
                        if msg_widget is None:
                            msg_widget = Static(classes="message")
                            await chat_log.mount(msg_widget)
                            chat_log.scroll_end(animate=False)
                        try:
                            ts_display = self._format_timestamp(assistant_ts)
                            title = f"[red]{self._role_icon('Assistant')} Assistant {ICON_ERROR}[/red]"
                            if ts_display:
                                title += f" [dim]· {ts_display}[/dim]"
                            msg_widget.update(
                                self._flat(f"[red]{message}[/red]", title)
                            )
                        except Exception:
                            logger.warning("Failed to update error panel")
                    case AgentDone(full_text):
                        stop_spinner()
                        if think_widget is not None:
                            think_widget.title = "💭 Thinking"
                            think_widget.collapsed = True
                        if msg_widget is None:
                            msg_widget = Static(classes="message")
                            await chat_log.mount(msg_widget)
                            chat_log.scroll_end(animate=False)
                        full_response = full_text
                        if not full_response:
                            try:
                                ts_display = (
                                    self._format_timestamp(assistant_ts)
                                    if full_response == ""
                                    else ""
                                )
                                title = f"[yellow]{self._role_icon('Assistant')} Assistant {ICON_EMPTY}[/yellow]"
                                if ts_display:
                                    title += f" [dim]· {ts_display}[/dim]"
                                msg_widget.update(
                                    self._flat(
                                        "[red]No response received. Is the backend running and the model available?[/red]",
                                        title,
                                    )
                                )
                            except Exception:
                                logger.warning("Failed to update no-response panel")
                        if full_response:
                            self.messages.append(
                                Message(
                                    role="assistant",
                                    content=full_response,
                                    timestamp=assistant_ts,
                                )
                            )
                        else:
                            logger.debug(
                                "Skipping empty assistant message append "
                                "(would poison next request under strict validators)"
                            )

            # Auto-prune oldest messages when exceeding max_messages
            if len(self.messages) > self.config.max_messages:
                system = [m for m in self.messages if m.role == "system"]
                rest = [m for m in self.messages if m.role != "system"]
                keep_count = self.config.max_messages - len(system)
                start = max(0, len(rest) - keep_count)
                # Walk backward until the slice begins at a user turn so we never
                # leave an orphan assistant(tool_use) / role=tool block at index 0.
                while start > 0 and rest[start].role != "user":
                    start -= 1
                pruned = system + rest[start:]
                self.messages = sanitize_tool_history(pruned)
        finally:
            self._save_session()
            stop_spinner()
            input_widget.disabled = False

    # --- UI helpers ---

    def _format_timestamp(self, timestamp: str | None) -> str:
        if timestamp is None:
            return ""
        try:
            dt = datetime.fromisoformat(timestamp)
            return dt.astimezone().strftime("%b %d %H:%M")
        except (ValueError, TypeError):
            logger.debug("Failed to parse timestamp: %s", timestamp)
            return ""

    def _add_message(
        self,
        role: str,
        text: str,
        border_style: str | None = None,
        timestamp: str | None = None,
    ) -> None:
        color = border_style or self._role_color(role)
        icon = self._role_icon(role)
        title = f"[{color}]{icon} {role}[/{color}]"
        ts = self._format_timestamp(timestamp)
        if ts:
            title += f" [dim]· {ts}[/dim]"
        chat_log = self.query_one("#chat-log")
        static = Static(self._flat(text, title), classes="message")
        static.plain_text = text
        chat_log.mount(static)
        chat_log.scroll_end(animate=False)

    def _remove_welcome(self) -> None:
        try:
            self.query_one("#welcome").remove()
        except Exception:
            logger.debug("Failed to remove welcome widget")

    async def _permission_callback(self, description: str) -> str:
        """Shared confirm-callback for the root agent and any dispatched
        sub-agents. Serialized with a lock so concurrent sub-agents queue
        their permission prompts instead of racing on the same modal.
        """
        async with self._permission_lock:
            return await self.push_screen_wait(PermissionScreen(description))

    def _register_dispatch_tool(self) -> None:
        """Register the `dispatch` built-in tool that fans a batch of tasks
        out to pooled sub-agents concurrently.
        """
        assert self.agent_pool is not None
        agent_names = self.agent_pool.list_agents("default")
        if not agent_names:
            logger.debug(
                "Agent pool has no delegate-able agents; skipping dispatch tool"
            )
            return

        self._builtin_tools.register_tool(
            name="dispatch",
            description=(
                "Dispatch one or more tasks to specialist sub-agents to run "
                "CONCURRENTLY (in parallel, not sequentially). Use this instead "
                "of calling sub-agents one at a time. Available agents: "
                + ", ".join(agent_names)
            ),
            parameters={
                "type": "object",
                "properties": {
                    "tasks": {
                        "type": "array",
                        "description": "The batch of tasks to run in parallel.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "agent": {
                                    "type": "string",
                                    "enum": agent_names,
                                    "description": "Name of the pooled agent to run this task.",
                                },
                                "task": {
                                    "type": "string",
                                    "description": "The task/instructions for this agent.",
                                },
                            },
                            "required": ["agent", "task"],
                        },
                    },
                },
                "required": ["tasks"],
            },
            handler=self._dispatch_tasks,
        )

    async def _dispatch_tasks(self, tasks: list[dict]) -> str:
        assert self.agent_pool is not None
        if not tasks:
            return "Error: dispatch called with no tasks"

        available_tools = await self.mcp_manager.get_available_tools()
        sub_tools = [t for t in available_tools if t.get("name") != "builtin__dispatch"]

        now = datetime.now(timezone.utc).isoformat()
        self._sub_runs = []
        for i, spec in enumerate(tasks):
            self._sub_runs.append(
                SubAgentRun(
                    task_id=f"run-{i + 1}",
                    agent_name=str(spec.get("agent", "")),
                    task=str(spec.get("task", "")),
                    started_at=now,
                )
            )
        self._sync_sub_agent_tree()
        self._start_sub_tree_timer()

        async def run_one(idx: int) -> str:
            run = self._sub_runs[idx]
            sub_agent = self.agent_pool.select_agent("default", run.agent_name)
            sub_messages = [Message(role="user", content=run.task)]
            return await stream_sub_agent_run(
                run,
                sub_agent.process(
                    sub_messages,
                    tools=sub_tools,
                    confirm_callback=self._permission_callback,
                ),
            )

        async def run_task(idx: int) -> tuple[str, str]:
            run = self._sub_runs[idx]
            try:
                result = await run_one(idx)
                return run.agent_name, result
            except ValueError as e:
                run.status = "error"
                run.activity = f"error: {e}"
                return run.agent_name, f"Error: {e}"
            except Exception as e:
                logger.exception("Dispatched agent '%s' failed", run.agent_name)
                run.status = "error"
                run.activity = f"error: {e}"
                return run.agent_name, f"Error: {e}"

        try:
            results = await asyncio.gather(*(run_task(i) for i in range(len(tasks))))
        finally:
            self._sync_sub_agent_tree()
            self._stop_sub_tree_timer()
        return "\n\n".join(f"## {name}\n{text}" for name, text in results)

    def _run_label(self, run: SubAgentRun) -> str:
        status = {
            "running": "[bold cyan]●[/bold cyan] running",
            "done": "[bold green]✓[/bold green] done",
            "error": "[bold red]✗[/bold red] error",
        }.get(run.status, run.status)
        activity = run.activity or "queued"
        return f"{run.agent_name}  {status}  [dim]({activity})[/dim]"

    def _sync_sub_agent_tree(self) -> None:
        if self.agent_pool is None:
            return
        try:
            tree = self.query_one("#sub-agent-tree", Tree)
        except Exception:
            return
        tree.root.label = "Root (you)"
        tree.root.data = None
        tree.clear()
        self._sub_tree_nodes = {}
        for run in self._sub_runs:
            node = tree.root.add(self._run_label(run), data=run)
            self._sub_tree_nodes[run.task_id] = node
        tree.root.expand()
        tree.refresh()

    def _refresh_sub_agent_tree(self) -> None:
        if self.agent_pool is None or not self._sub_runs:
            return
        try:
            tree = self.query_one("#sub-agent-tree", Tree)
        except Exception:
            return
        for run in self._sub_runs:
            node = self._sub_tree_nodes.get(run.task_id)
            if node is not None:
                node.label = self._run_label(run)
        tree.refresh()

    def _start_sub_tree_timer(self) -> None:
        if self._sub_tree_timer is None:
            self._sub_tree_timer = self.set_interval(0.5, self._refresh_sub_agent_tree)

    def _stop_sub_tree_timer(self) -> None:
        timer = self._sub_tree_timer
        if timer is not None:
            timer.stop()
            self._sub_tree_timer = None

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        run = event.node.data
        if isinstance(run, SubAgentRun):
            self.push_screen(SubAgentViewScreen(run))

    def _verify_offline(self) -> None:
        """Startup check: warn about any outbound calls configured."""
        warnings: list[str] = []

        if not self.config.offline_mode:
            warnings.append("Offline mode is disabled")

        for mcp_name, mcp_cfg in self.mcp_manager.servers.items():
            if mcp_cfg.transport == "http":
                warnings.append(
                    f"MCP server '{mcp_name}' uses HTTP transport: {mcp_cfg.url}"
                )

        web_tools = [
            "websearch",
            "fetch",
            "search_wikipedia",
            "search_github",
            "search_arxiv",
        ]
        enabled_web = [t for t in web_tools if t in self._builtin_tools._tools]
        if enabled_web and not self.config.offline_mode:
            warnings.append(f"Web tools available: {', '.join(enabled_web)}")

        if warnings:
            msg = (
                "[bold yellow]Offline verification warnings:[/bold yellow]\n"
                + "\n".join(f"  • {w}" for w in warnings)
            )
        else:
            msg = "[bold green]Offline verification passed: no outbound calls configured.[/bold green]"

        self.notify(msg, severity="warning" if warnings else "information", timeout=10)
        self._add_message("System", msg)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="oli — Multi-Backend AI Chat TUI")
    parser.add_argument("--model", default=None, help="Model to use")
    parser.add_argument(
        "--url",
        default="http://localhost:11434",
        help="Backend server URL (default: http://localhost:11434)",
    )
    parser.add_argument(
        "--profile",
        default="default",
        help="Agent profile to load from profiles/ (default: default)",
    )
    resume_group = parser.add_mutually_exclusive_group()
    resume_group.add_argument(
        "--resume-last",
        action="store_true",
        help="Resume the most recent session on startup",
    )
    resume_group.add_argument(
        "-s",
        "--load-session",
        default=None,
        help="Load a specific session by UUID on startup",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview destructive actions without executing them",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Enable offline mode (block network tools)",
    )
    parser.add_argument(
        "--no-offline",
        action="store_true",
        help="Disable offline mode (allow network tools)",
    )
    parser.add_argument(
        "--verify-offline",
        action="store_true",
        help="Startup check: verify no outbound calls are configured",
    )
    parser.add_argument(
        "--use-pool",
        action="store_true",
        help="Enable agent pooling (root agent can dispatch tasks to sub-agents defined in agents.yaml)",
    )
    return parser


def main() -> None:
    args = _build_arg_parser().parse_args()
    app = OliBot(
        model=args.model,
        base_url=args.url,
        profile=args.profile,
        resume_last=args.resume_last,
        load_session=args.load_session,
        dry_run=args.dry_run,
        use_pool=args.use_pool,
    )
    if args.offline:
        app.config.offline_mode = True
    if args.no_offline:
        app.config.offline_mode = False
    app.mcp_manager._offline_mode = app.config.offline_mode

    if args.verify_offline:
        app._verify_offline()

    app.run()

    print(f"Resume this session with: -s {app.current_session_id} or --resume-last")


if __name__ == "__main__":
    main()
