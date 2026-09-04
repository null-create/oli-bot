from __future__ import annotations

"""Built-in tool definitions that run in-process (not via MCP)."""

import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..sessions import Session
    from ..backends import ModelBackend
    from ..profiles.permissions import ProfilePermissionEnforcer
    from ..models import ImageAttachment

from ..config import AppConfig
from .permissions import PermissionGate
from .truncation import TruncationManager, TruncationConfig

logger = logging.getLogger(__name__)

DESTRUCTIVE_TOOLS: set[str] = {
    "write_file",
    "edit_file",
    "download_file",
    "upload_file",
    "run_command",
}

NETWORK_TOOLS: set[str] = {
    "websearch",
    "fetch",
    "download_file",
    "upload_file",
    "search_wikipedia",
    "search_github",
    "search_arxiv",
    "search_stackoverflow",
    "search_open_library",
    "extract_article",
}

READ_ONLY_TOOLS: set[str] = {
    "read_file",
    "view_image",
    "glob",
    "grep",
    "list_directory",
    "tree",
    "run_command",
    "websearch",
    "fetch",
    "think",
    "compare",
    "search_stackoverflow",
    "search_open_library",
    "extract_article",
}
# Used by "plan" mode: research tools plus notebook/todowrite for drafting
# and saving plans, but no write_file/edit_file/other destructive tools.
PLAN_TOOLS: set[str] = READ_ONLY_TOOLS | {"notebook", "todowrite"}


class BuiltinToolManager:
    """Manages built-in tools defined as Python functions with an internal dict."""

    def __init__(
        self,
        session: Optional["Session"] = None,
        backend: Optional[ModelBackend] = None,
        config: Optional[AppConfig] = None,
        permission_enforcer: Optional["ProfilePermissionEnforcer"] = None,
    ):
        self._tools: Dict[str, Dict[str, Any]] = {}
        self._session = session
        self._backend = backend
        self._config = config or AppConfig()
        self._permission_enforcer = permission_enforcer
        self._model_tier: str = "large"
        cfg = self._config
        self._truncation = TruncationManager(
            TruncationConfig(
                max_chars_small=cfg.truncation_max_chars_small,
                max_chars_large=cfg.truncation_max_chars_large,
            )
        )
        self._todos: list[dict] = []
        self._todo_change_callback: Optional[Callable[[list[dict]], None]] = None
        self._sub_todo_change_callback: Optional[Callable] = None
        self._pending_attachments: list["ImageAttachment"] = []
        self._pending_caption: str = ""
        self._register_default_tools()
        self._gate = PermissionGate(
            session=self._session,
            config=self._config,
            permission_enforcer=self._permission_enforcer,
            known_tools=self._tools.keys(),
            destructive_tools=DESTRUCTIVE_TOOLS,
            network_tools=NETWORK_TOOLS,
        )

    @property
    def model_tier(self) -> str:
        return self._model_tier

    @model_tier.setter
    def model_tier(self, tier: str) -> None:
        self._model_tier = tier

    async def _call_model_async(
        self,
        prompt: str,
        max_tokens: int = 512,
        model: Optional[str] = None,
    ) -> str:
        if self._backend is None:
            return "Error: No backend configured for model-based tool calls."

        effective_model = model or self._config.lightweight_model
        try:
            from ..backends import Message

            messages = [Message(role="user", content=prompt)]
            response = await self._backend.generate(
                model=effective_model,
                messages=messages,
                tools=None,
                max_tokens=max_tokens,
                temperature=self._config.temperature,
            )
            if response.finish_reason == "error":
                return f"Error: Model call failed: {response.error}"
            return response.content.strip()
        except Exception as e:
            logger.exception("Model-based tool call failed")
            return f"Error: Model-based tool call failed: {e}"

    def _register_default_tools(self) -> None:
        from .files import register_tools as reg_files
        from .directories import register_tools as reg_dirs
        from .web import register_tools as reg_web
        from .shell import register_tools as reg_shell
        from .parsing import register_tools as reg_parsing
        from .memory import register_tools as reg_memory

        reg_files(self)
        reg_dirs(self)
        reg_web(self)
        reg_shell(self)
        reg_parsing(self)
        reg_memory(self)

    def register_tool(
        self,
        name: str,
        description: str,
        parameters: Dict[str, Any],
        handler: Callable[..., str],
    ) -> None:
        self._tools[name] = {
            "name": name,
            "description": description,
            "parameters": parameters,
            "handler": handler,
        }
        gate = getattr(self, "_gate", None)
        if gate is not None:
            gate.refresh_known_tools(self._tools.keys())

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": f"builtin__{name}",
                "description": info["description"],
                "parameters": info["parameters"],
                "server": "builtin",
            }
            for name, info in self._tools.items()
        ]

    def get_readonly_tool_definitions(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": f"builtin__{name}",
                "description": info["description"],
                "parameters": info["parameters"],
                "server": "builtin",
            }
            for name, info in self._tools.items()
            if name in READ_ONLY_TOOLS
        ]

    def get_plan_tool_definitions(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": f"builtin__{name}",
                "description": info["description"],
                "parameters": info["parameters"],
                "server": "builtin",
            }
            for name, info in self._tools.items()
            if name in PLAN_TOOLS
        ]

    def get_todos(self):
        """Get the current todo list as a TodoListState with computed counts.

        Returns a TodoListState populated with the current todos and breakdown
        by status. Used by the TUI to render the todo widget dynamically.
        """
        from datetime import datetime
        from ..models import TodoItem, TodoListState

        items = [
            TodoItem(
                content=t.get("content", ""),
                status=t.get("status", "pending"),
                priority=t.get("priority", "medium"),
            )
            for t in self._todos
        ]

        by_status = {}
        for item in items:
            by_status[item.status] = by_status.get(item.status, 0) + 1

        return TodoListState(
            items=items,
            last_updated=datetime.now().strftime("%H:%M:%S"),
            total_count=len(items),
            pending_count=by_status.get("pending", 0),
            in_progress_count=by_status.get("in_progress", 0),
            completed_count=by_status.get("completed", 0),
            cancelled_count=by_status.get("cancelled", 0),
        )

    def set_todo_callback(self, callback: Optional[Callable[[list[dict]], None]]) -> None:
        """Register a callback that fires whenever the root agent's todo list changes."""
        self._todo_change_callback = callback

    def set_sub_todo_callback(self, callback: Optional[Callable]) -> None:
        """Register a callback that fires whenever a sub-agent's todo list changes.

        The callback receives ``(run: SubAgentRun, todos: list[dict])``.
        """
        self._sub_todo_change_callback = callback

    def attach_image(self, attachment: "ImageAttachment") -> None:
        """Push an image attachment onto the pending queue for the current tool call."""
        self._pending_attachments.append(attachment)

    def set_pending_caption(self, caption: str) -> None:
        """Set the caption/question for the synthetic follow-up user message."""
        self._pending_caption = caption or ""

    def drain_attachments(self) -> tuple[list["ImageAttachment"], str]:
        """Return (attachments, caption) produced by the last tool call, then clear."""
        drained = list(self._pending_attachments)
        caption = self._pending_caption
        self._pending_attachments.clear()
        self._pending_caption = ""
        return drained, caption

    async def call_tool(
        self,
        name: str,
        arguments: Dict[str, Any],
        confirm_callback: Optional[Callable[[str], Any]] = None,
    ) -> str:
        decision = self._gate.evaluate(name, arguments)

        if decision.outcome == "deny":
            return f"Error: {decision.reason}"

        if decision.outcome == "prompt":
            if confirm_callback is not None:
                user = await confirm_callback(decision.description)
                match user:
                    case "once":
                        pass
                    case "session":
                        if self._session is not None and decision.scope is not None:
                            self._session.grant(decision.scope, session=True)
                    case _:
                        return "Error: Permission denied by user"
            # Re-run with the session gate skipped so we don't prompt again.
            decision = self._gate.evaluate(name, arguments, skip_session=True)
            if decision.outcome == "deny":
                return f"Error: {decision.reason}"

        if decision.outcome == "preview":
            return decision.preview

        self._pending_attachments.clear()
        self._pending_caption = ""
        try:
            handler = self._tools[name]["handler"]
            result = handler(**arguments)
            if asyncio.iscoroutine(result):
                result = await result
            result = str(result)
            result = self._truncation.truncate(result, tier=self._model_tier)
            return result
        except TypeError as e:
            # Likely the model passed arguments intended for a different tool.
            # Return a structured, actionable message so the model can self-correct.
            expected = list(
                self._tools[name]["parameters"].get("properties", {}).keys()
            )
            logger.exception("Built-in tool '%s' failed", name)
            return (
                f"Error: tool '{name}' received an unexpected argument. {e}. "
                f"Expected parameters: {expected}. "
                f"Check that you are calling the correct tool with the correct arguments."
            )
        except Exception as e:
            logger.exception("Built-in tool '%s' failed", name)
            return f"Error executing {name}: {e}"
