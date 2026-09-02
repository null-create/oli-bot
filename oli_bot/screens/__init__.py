"""Textual modal screens for the oli TUI.

Split out of the historical monolithic ``views.py`` — see [AGENTS.md](../AGENTS.md).
``views.py`` is retained as a re-export shim so existing imports keep working.
"""

from __future__ import annotations

from .config_screen import ConfigScreen
from .confirm import ConfirmScreen
from .input_prompt import InputPrompt
from .mcp_setup import MCPSetupScreen
from .model_picker import ModelPicker
from .permission import PermissionScreen
from .server_list import ServerListScreen
from .session_list import SessionListScreen
from .sub_agent_view import SubAgentViewScreen
from .taglines import TAGLINES
from .todo_widget import TodoWidget
from .workspace_list import WorkspaceListScreen

__all__ = [
    "ConfigScreen",
    "ConfirmScreen",
    "InputPrompt",
    "MCPSetupScreen",
    "ModelPicker",
    "PermissionScreen",
    "ServerListScreen",
    "SessionListScreen",
    "SubAgentViewScreen",
    "TAGLINES",
    "TodoWidget",
    "WorkspaceListScreen",
]
