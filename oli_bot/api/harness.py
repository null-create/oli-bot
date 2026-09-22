"""Agent harness construction for the API server.

Builds the shared ``Agent`` exactly like the TUI does (minus the Textual
UI), wires the todo relay, and provides the ``dispatch`` tool handler factory
for agent pooling.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, List

from ..agent import Agent, AgentPool, run_agent_dispatch
from ..backends import ModelBackend, create_model_backend
from ..config import AppConfig
from ..mcp_client import MCPClientManager
from ..models import SubAgentRun
from ..sessions import Session, is_sensitive_path
from ..tools.manager import BuiltinToolManager

logger = logging.getLogger(__name__)


def _select_model(config: AppConfig, backend: ModelBackend) -> None:
    """Fill in the active model if the factory defaulted to empty."""
    if backend.model and str(backend.model):
        return
    if config.backend == "openai":
        backend.model = config.openai_model
    elif config.backend == "huggingface":
        backend.model = config.huggingface_model
    elif config.backend == "transformers":
        backend.model = config.transformers_model
    else:
        backend.model = config.ollama_model
    backend.model = str(backend.model) or None


def _build_agent(config: AppConfig, mode: str, profile: str) -> Agent:
    """Construct the shared Agent harness exactly like ``chat.py`` does,
    minus the Textual TUI."""
    url = config.ollama_base_url
    backend = create_model_backend(url, config.backend, None)
    _select_model(config, backend)

    cwd = Path.cwd()
    session = Session(workspace=None if is_sensitive_path(cwd) else cwd)
    builtin_tools = BuiltinToolManager(
        session=session,
        backend=backend,
        config=config,
    )
    mcp_manager = MCPClientManager(
        config=config,
        builtin_tools=builtin_tools,
        offline_mode=config.offline_mode,
        session=session,
    )
    agent = Agent(
        role="root",
        backend=backend,
        mcp_manager=mcp_manager,
        profile_name=profile,
        config=config,
    )
    agent.set_mode(mode)
    agent._session = session
    return agent


async def _api_confirm(description: str) -> str:
    """Auto-allow every permission scope (API mode has no interactive prompt)."""
    return "session"


def _wire_todo_relay(agent: Agent) -> None:
    """Relay ``builtin__todowrite`` updates to WebSocket clients.

    The tool manager invokes these synchronously from inside the tool handler;
    we append snapshots to ``mcp_manager.pending_todos`` which the socket loop
    drains after each agent event. ``task_id`` is present for sub-agent runs so
    the client can demux the update.
    """

    def _on_todos_changed(todos: list) -> None:
        agent.mcp_manager.pending_todos.append({"todos": list(todos)})

    def _on_sub_todos_changed(run: SubAgentRun, todos: list) -> None:
        agent.mcp_manager.pending_todos.append(
            {
                "todos": list(todos),
                "task_id": run.task_id,
                "agent_name": run.agent_name,
            }
        )

    agent.mcp_manager._builtin_tools.set_todo_callback(_on_todos_changed)
    agent.mcp_manager._builtin_tools.set_sub_todo_callback(_on_sub_todos_changed)


def make_dispatch_handler(agent: Agent, pool: AgentPool) -> Callable[[List[dict]], str]:
    """Build the API-server ``dispatch`` tool handler bound to ``agent``/``pool``.

    Fans a batch of tasks out to pooled sub-agents concurrently, mirroring the
    TUI's implementation. Sub-agent lifecycle events are pushed onto
    ``mcp_manager.sub_agent_queue`` (set by the agent's tool loop while the
    dispatch call is in flight) so the WebSocket relay streams them live.
    """

    async def _dispatch_tasks(tasks: List[dict]) -> str:
        if not tasks:
            return "Error: dispatch called with no tasks"

        available_tools = await agent.mcp_manager.get_available_tools()
        sub_tools = [t for t in available_tools if t.get("name") != "builtin__dispatch"]

        now = datetime.now(timezone.utc).isoformat()
        runs: List[SubAgentRun] = []
        for i, spec in enumerate(tasks):
            runs.append(
                SubAgentRun(
                    task_id=f"run-{i + 1}",
                    agent_name=str(spec.get("agent", "")),
                    pool_name=str(spec.get("pool", "default")),
                    task=str(spec.get("task", "")),
                    started_at=now,
                )
            )

        return await run_agent_dispatch(
            pool=pool,
            runs=runs,
            tools=sub_tools,
            confirm_callback=_api_confirm,
            event_sink=agent.mcp_manager.sub_agent_queue,
        )

    return _dispatch_tasks
