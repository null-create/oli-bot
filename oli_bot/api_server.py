"""Compatibility shim for the older single-module API server layout.

The API server now lives under :mod:`oli_bot.api` (an ``APIRouter`` package
with an app factory). This module re-exports the public surface of the old
``oli_bot.api_server`` module so existing importers — the ``oli-server``
console script, ``python -m oli_bot.api_server``, and tests — keep working
unchanged.

Importing this module is cheap: ``app`` is built via ``create_app()`` and
agent/backend/MCP state is only constructed by ``main()`` / the app lifespan.
"""

from .agent import (
    Agent,
    AgentEvent,
    AgentPool,
    register_dispatch_tool,
    run_agent_dispatch,
    sanitize_tool_history,
)
from .backends import ModelBackend, create_model_backend
from .config import AppConfig, configs
from .models import (
    AssistantResponse,
    Done,
    Error,
    ImageAttachment,
    MCPServerConfig,
    Message,
    StreamChunk,
    SubAgentCompleted,
    SubAgentEvent,
    SubAgentProgress,
    SubAgentRun,
    SubAgentStarted,
    ThinkingChunk,
    ToolCallExecuting,
    ToolCallResult,
    UsageEvent,
    ChatCompletionMessage,
    ChatCompletionRequest,
)
from .sessions import ConversationStore, Session, WorkspaceManager
from .settings import SettingsManager
from .tools.manager import BuiltinToolManager

from .api.app import create_app, init_state
from .api.constants import SESSION_SERVER
from .api.convert import (
    _decode_data_uri,
    _event_to_frame,
    _media_type_from_data_uri,
    _to_message,
)
from .api.deps import get_agent, get_config, get_lock, get_store
from .api.errors import AgentError, error_body
from .api.harness import (
    _api_confirm,
    _build_agent,
    _select_model,
    _wire_todo_relay,
    make_dispatch_handler,
)
from .api.runner import (
    _collect_response,
    _completion_id,
    _resolve_tools,
    _stream_response,
    _usage,
)
from .api.session_service import (
    _load_conversation,
    _persist_session,
    _session_created_frame,
    _session_meta,
)
from .api.routers.config import _FLAT_TO_NESTED, _flat_to_nested, _nested_to_flat
from .api.routers.mcp import _mcp_list, _validate_mcp_config
from .api.routers.workspace import (
    _FS_LIST_LIMIT,
    _list_dir,
    _workspace_state,
)

from .api.__main__ import _print_banner

# Startup constants (mirrors the old module-level reads from ``configs``).
API_HOST = configs.api_host
API_PORT = configs.api_port
API_PROFILE = configs.api_profile
API_MODE = configs.api_mode

app = create_app()


def main() -> None:
    from .api.__main__ import main as _main

    _main()


if __name__ == "__main__":
    main()