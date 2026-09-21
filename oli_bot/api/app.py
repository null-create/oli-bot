"""FastAPI app factory, lifespan, and state initialization."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .errors import register_exception_handlers
from .routers import chat, config, health, mcp, sessions, workspace, ws

logger = logging.getLogger(__name__)


def init_state(app: FastAPI) -> None:
    """Build the shared agent/backend/MCP state onto ``app.state``.

    Called explicitly from ``main()`` (before the banner) and idempotently from
    the lifespan so ``TestClient``-driven code that never enters the lifespan
    can still initialize state on demand.
    """
    from ..agent import AgentPool, register_dispatch_tool
    from ..sessions import ConversationStore, WorkspaceManager
    from ..settings import SettingsManager
    from .harness import _build_agent, _wire_todo_relay, make_dispatch_handler

    config = SettingsManager().to_appconfig(SettingsManager().load())
    agent = _build_agent(config, mode=config.api_mode, profile=config.api_profile)
    app.state.config = config
    app.state.agent = agent
    app.state.lock = asyncio.Lock()
    app.state.session_store = ConversationStore()
    app.state.workspace_manager = WorkspaceManager()

    # Relay todo-list updates (from ``builtin__todowrite``) to WebSocket
    # clients. The tool manager invokes these synchronously; we just append
    # snapshots to a queue the socket loop drains after each agent event.
    _wire_todo_relay(agent)

    # Agent pooling: when enabled, build the pool and register the dispatch
    # tool exactly like the TUI so the root agent can fan work out to
    # specialist sub-agents. Sub-agent events flow to the WebSocket live.
    if config.use_agent_pool:
        try:
            pool = AgentPool(agent.mcp_manager, config=config)
            register_dispatch_tool(
                agent.mcp_manager._builtin_tools,
                pool,
                make_dispatch_handler(agent, pool),
            )
            app.state.agent_pool = pool
            logger.info("Agent pooling enabled: %s", list(pool.agent_pool.keys()))
            if not pool.has_agents():
                logger.error(
                    "Agent pooling enabled but no sub-agents loaded. "
                    "Checked $OLI_AGENTS_YAML, the package dir, the repo root, "
                    "and ~/.config/oli for agents.yaml. "
                    "The 'dispatch' tool will not be available."
                )
        except Exception as e:
            logger.error("Failed to build agent pool: %s", e)
            app.state.agent_pool = None
    else:
        app.state.agent_pool = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not hasattr(app.state, "agent"):
        init_state(app)
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="oli", version="1.0.0", lifespan=lifespan)
    register_exception_handlers(app)
    for router in (
        health.router,
        chat.router,
        ws.router,
        sessions.router,
        config.router,
        mcp.router,
        workspace.router,
    ):
        app.include_router(router)
    return app