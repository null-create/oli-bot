from __future__ import annotations

import logging

from rich.console import Group
from rich.markdown import Markdown
from rich.padding import Padding
from rich.text import Text as RichText
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Collapsible, Label, Static

from ..models import (
    AssistantResponse,
    Error,
    StreamChunk,
    SubAgentRun,
    ThinkingChunk,
    ToolCallExecuting,
    ToolCallResult,
)

logger = logging.getLogger(__name__)


class SubAgentViewScreen(ModalScreen[None]):
    """Live view of a dispatched sub-agent's work.

    Replays the events collected on the shared ``SubAgentRun`` (populated by
    ``agent.stream_sub_agent_run``) on a timer, so streaming text, thinking,
    and tool calls render in real time while the dispatch is still running.
    """

    CSS = """
    #subagent-view-container {
        align: center middle;
        width: 90%;
        height: 90%;
        border: round $primary;
        background: $surface;
    }

    #subagent-view-title {
        padding: 1;
        text-style: bold;
        content-align: center middle;
    }

    #subagent-view-scroll {
        height: 1fr;
        padding: 0 1;
        overflow-y: auto;
    }

    #subagent-tools {
        height: auto;
    }

    #subagent-view-footer {
        height: 3;
        align: center middle;
        padding: 0 1;
    }

    Button {
        margin: 0 1;
        min-width: 12;
    }

    .subagent-hint {
        color: $text-muted;
        content-align: center middle;
    }
    """

    BINDINGS = [("escape", "back", "Back to root")]

    def __init__(self, run: SubAgentRun):
        super().__init__()
        self._run = run
        self._cursor = 0
        self._response_text = ""
        self._think_text = ""
        self._think_widget: Collapsible | None = None
        self._think_inner: Static | None = None
        self._response_widget: Static | None = None
        self._tools_container: Vertical | None = None
        self._tool_widgets: dict[str, Static] = {}
        self._timer = None

    def compose(self) -> ComposeResult:
        with Container(id="subagent-view-container"):
            yield Label(
                f"[bold]{self._run.agent_name}[/bold] — {self._run.status}",
                id="subagent-view-title",
            )
            with VerticalScroll(id="subagent-view-scroll"):
                yield Static(
                    f"[dim]Task ·[/dim] {self._run.task}",
                    classes="subagent-message",
                )
                yield Static(classes="subagent-response", id="subagent-response")
                with Vertical(id="subagent-tools"):
                    pass
            with Horizontal(id="subagent-view-footer"):
                yield Button("Back to root", variant="primary", id="subagent-back")
                yield Label("\u2190 Esc to return", classes="subagent-hint")

    def on_mount(self) -> None:
        self._response_widget = self.query_one("#subagent-response", Static)
        self._tools_container = self.query_one("#subagent-tools", Vertical)
        self.query_one("#subagent-back", Button).focus()
        self._timer = self.set_interval(0.25, self._tick)

    def on_unmount(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    def _tick(self) -> None:
        events = self._run.events
        while self._cursor < len(events):
            event = events[self._cursor]
            self._cursor += 1
            self._handle_event(event)
        if self._run.status != "running":
            self._finalize()

    def _handle_event(self, event) -> None:
        if isinstance(event, ThinkingChunk):
            self._think_text += event.text
            self._render_thinking()
        elif isinstance(event, StreamChunk):
            self._response_text += event.text
            self._render_response()
        elif isinstance(event, AssistantResponse):
            self._response_text = event.content
            self._render_response()
        elif isinstance(event, ToolCallExecuting):
            self._add_tool_call(event)
        elif isinstance(event, ToolCallResult):
            self._add_tool_result(event)
        elif isinstance(event, Error):
            self._response_text += f"\n[red]{event.message}[/red]"
            self._render_response()

    def _finalize(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        if self._run.full_text:
            self._response_text = self._run.full_text
            self._render_response()
        status_color = "green" if self._run.status == "done" else "red"
        self.query_one("#subagent-view-title", Label).update(
            f"[bold]{self._run.agent_name}[/bold] — "
            f"[{status_color}]{self._run.status}[/{status_color}]"
        )

    def _render_thinking(self) -> None:
        if self._think_widget is None:
            self._think_inner = Static(classes="subagent-message")
            self._think_widget = Collapsible(
                self._think_inner,
                title="\U0001f4ad Thinking...",
                collapsed=False,
            )
            self.query_one("#subagent-view-scroll", VerticalScroll).mount(
                self._think_widget, before="#subagent-response"
            )
        try:
            try:
                rendered = Markdown(self._think_text)
            except Exception:
                rendered = self._think_text
            if self._think_inner is not None:
                self._think_inner.update(rendered)
        except Exception:
            logger.warning("Failed to update sub-agent thinking panel")
        self._scroll_end()

    def _render_response(self) -> None:
        if self._response_widget is None or not self._response_text:
            return
        try:
            try:
                rendered = Markdown(self._response_text)
            except Exception:
                rendered = self._response_text
            header = RichText.from_markup("[bold green]\u25cf Assistant[/bold green]")
            self._response_widget.update(Group(header, Padding(rendered, (0, 0, 0, 2))))
        except Exception:
            logger.warning("Failed to update sub-agent response panel")
        self._scroll_end()

    def _add_tool_call(self, event: ToolCallExecuting) -> None:
        if event.parameters:
            params = ", ".join(f"{k}={v}" for k, v in event.parameters.items())
            base = f"[bold]{event.name}[/bold]  [dim]· {params}[/dim]"
        else:
            base = f"[bold]{event.name}[/bold]"
        content = f"[dim]◐[/dim] {base}"
        widget = Static(content, classes="subagent-message")
        widget.plain_text = content
        widget._tool_base = base
        if self._tools_container is not None:
            self._tools_container.mount(widget)
        self._tool_widgets[event.name] = widget
        self._scroll_end()

    def _add_tool_result(self, event: ToolCallResult) -> None:
        widget = self._tool_widgets.get(event.name)
        if widget is None:
            return
        is_error = str(event.result).lstrip().startswith("Error")
        icon = "[red]\u2717[/red]" if is_error else "[green]\u2713[/green]"
        base = getattr(widget, "_tool_base", f"[bold]{event.name}[/bold]")
        updated = f"{icon} {base}"
        widget.update(updated)
        widget.plain_text = updated
        self._scroll_end()

    def _scroll_end(self) -> None:
        try:
            self.query_one("#subagent-view-scroll", VerticalScroll).scroll_end(
                animate=False
            )
        except Exception:
            logger.debug("Failed to scroll sub-agent view")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "subagent-back":
            self.dismiss(None)

    def action_back(self) -> None:
        self.dismiss(None)


__all__ = ["SubAgentViewScreen"]
