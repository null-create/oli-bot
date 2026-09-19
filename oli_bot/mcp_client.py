import json
import logging
from contextlib import AsyncExitStack

from oli_bot.config import AppConfig

from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

if TYPE_CHECKING:
    from .profiles.permissions import ProfilePermissionEnforcer

from mcp.client import Client
from mcp.client.stdio import stdio_client, StdioServerParameters

from .tools.manager import BuiltinToolManager
from .tools.permissions import PermissionDecision
from .profiles.permissions import ProfilePermissionEnforcer
from .sessions import Session
from .models import MCPServerConfig

logger = logging.getLogger(__name__)


class MCPToolManager:
    """Manages permissions for external MCP tools, delegating to a ProfilePermissionEnforcer if available."""

    def __init__(
        self,
        config: Optional[AppConfig] = None,
        session: Optional["Session"] = None,
        mcp_servers: Optional[Dict[str, Any]] = None,
        permission_enforcer: Optional["ProfilePermissionEnforcer"] = None,
    ):
        self._config = config
        self._session = session
        self._mcp_servers = mcp_servers or {}
        self._permission_enforcer = permission_enforcer

    def _evaluate_permission(
        self,
        name: str,
        arguments: Dict[str, Any],
        skip_session: bool = False,
        permission_enforcer: Optional["ProfilePermissionEnforcer"] = None,
    ) -> PermissionDecision:
        if name not in self._mcp_servers:
            decision = PermissionDecision(
                outcome="deny",
                reason=f"Unknown tool '{name}'",
                source="registry",
            )
            logger.info("permission deny: tool=%s source=%s", name, decision.source)
            return decision

        enforcer = permission_enforcer or self._permission_enforcer
        if enforcer is not None:
            if not enforcer.check_tool(name):
                decision = PermissionDecision(
                    outcome="deny",
                    reason=(
                        f"Tool '{name}' is not permitted "
                        f"by the active profile's permission manifest."
                    ),
                    source="profile",
                )
                logger.info("permission deny: tool=%s source=%s", name, decision.source)
                return decision

        if not skip_session and self._session is not None:
            scope = self._session.needs_permission(name, arguments)
            if scope:
                decision = PermissionDecision(
                    outcome="prompt",
                    scope=scope,
                    description=f"Permission required for tool '{name}' with args {arguments} with scope '{scope}'",
                    source="session",
                )
                logger.info(
                    "permission prompt: tool=%s scope=%s source=%s",
                    name,
                    scope,
                    decision.source,
                )
                return decision

        if self._config.offline_mode:
            decision = PermissionDecision(
                outcome="deny",
                reason=(
                    "Network access blocked by offline mode. "
                    "Use /config to disable offline mode, or restart without --offline."
                ),
                source="offline",
            )
            logger.info("permission deny: tool=%s source=%s", name, decision.source)
            return decision

        if self._config.dry_run:
            args_str = ", ".join(f"{k}={v!r}" for k, v in arguments.items())
            preview = f"[DRY RUN] Would execute `{name}({args_str})` — skipped"
            decision = PermissionDecision(
                outcome="preview",
                preview=preview,
                source="dry_run",
            )
            logger.info("permission preview: tool=%s source=%s", name, decision.source)
            return decision

        logger.debug("permission allow: tool=%s", name)
        return PermissionDecision(outcome="allow", source="allow")

    async def check_permission(
        self,
        server_name: str,
        tool_name: str,
        confirm_callback: Optional[Callable[[str], Any]] = None,
    ) -> str:
        if self._permission_enforcer is None:
            return "Error: No permission enforcer configured for MCP tools."

        if not self._mcp_servers or server_name not in self._mcp_servers:
            return f"Error: Unknown server: {server_name}"

        decision = self._evaluate_permission(
            tool_name,
            arguments={},
            skip_session=False,
            permission_enforcer=self._permission_enforcer,
        )
        if decision.outcome == "deny":
            return f"Error: tool {tool_name} denied by permissions enforcer"
        if decision.outcome == "preview":
            return decision.preview
        if decision.outcome == "prompt":
            if not confirm_callback:
                return "Error: Permission prompt required but no confirm_callback was provided"
            user = await confirm_callback("prompt")
            match user:
                case "once":
                    pass
                case "session":
                    decision = PermissionDecision(
                        outcome="approve",
                        reason=f"Permission approved for tool: {tool_name}",
                        source="user",
                        scope=f"tool:{tool_name}",
                    )
                    if self._session is not None:
                        self._session.grant(decision.scope, session=True)
                case _:
                    return "Error: Permission denied by user"
        return "Permission granted"


class MCPClientManager:
    def __init__(
        self,
        config: Optional["AppConfig"] = None,
        config_path: str | None = None,
        builtin_tools: Optional["BuiltinToolManager"] = None,
        offline_mode: bool = True,
        session: Optional["Session"] = None,
    ):
        if config_path is None:
            config_path = Path.joinpath(
                Path.home(), ".config", "oli", "mcp_servers.json"
            )
        self.config_path = config_path
        self.servers: Dict[str, MCPServerConfig] = {}
        self._clients: Dict[str, Client] = {}
        self._exit_stack = AsyncExitStack()
        self._builtin_tools = builtin_tools
        self._offline_mode = offline_mode
        self._warnings: List[str] = []
        # Live sub-agent event queue. Set/cleared by the agent's tool loop
        # only while a `builtin__dispatch` call is in flight; the dispatch
        # handler pushes SubAgent* events here so they can be drained
        # concurrently by the root agent's event stream.
        self.sub_agent_queue: Optional[Any] = None
        # Pending todo-list snapshots pushed by the tool manager's change
        # callbacks and drained by the API server's WebSocket relay. Each
        # entry is a dict: {"todos": [...], optional "task_id"/"agent_name"}.
        self.pending_todos: List[Dict[str, Any]] = []
        Path(self.config_path).parent.mkdir(parents=True, exist_ok=True)
        self._load_config()
        # Cached MCP tool listings, keyed by server name. Populated on first
        # request in `get_available_tools`, cleared by add/remove/disconnect.
        self._tool_cache: Dict[str, List[Dict[str, Any]]] = {}
        # Session object for permission checks. May be None if no session is active.
        self._session: Optional["Session"] = session
        # MCPToolManger for profile-based permission enforcement.
        # TODO: Not initialized with enforcer; should be replaced with a real one if available.
        self._tool_manager = MCPToolManager(
            config=config,
            session=self._session,
            mcp_servers=self.servers,
        )

    def _invalidate_tool_cache(self, server: str | None = None) -> None:
        if server is None:
            self._tool_cache.clear()
        else:
            self._tool_cache.pop(server, None)

    def add_server(
        self,
        name: str,
        command: str = "",
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        transport: str = "stdio",
        url: str = "",
    ) -> None:
        if name in self.servers:
            raise ValueError(f"Server '{name}' already exists")
        self.servers[name] = MCPServerConfig(
            name=name,
            transport=transport,
            command=command,
            args=args or [],
            env=env,
            url=url,
        )
        self._invalidate_tool_cache(name)
        self._save_config()

    def update_server(
        self,
        name: str,
        command: str = "",
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        transport: str = "stdio",
        url: str = "",
    ) -> None:
        if name not in self.servers:
            raise ValueError(f"Server '{name}' not found")
        # Drop any live client so the next call reopens a fresh connection.
        if name in self._clients:
            del self._clients[name]
        self.servers[name] = MCPServerConfig(
            name=name,
            transport=transport,
            command=command,
            args=args or [],
            env=env,
            url=url,
        )
        self._invalidate_tool_cache(name)
        self._save_config()

    def remove_server(self, name: str) -> None:
        if name not in self.servers:
            raise ValueError(f"Server '{name}' not found")
        if name in self._clients:
            del self._clients[name]
        del self.servers[name]
        self._invalidate_tool_cache(name)
        self._save_config()

    def list_servers(self) -> List[MCPServerConfig]:
        return list(self.servers.values())

    async def _fetch_mcp_tool_definitions(self) -> List[Dict[str, Any]]:
        tools: List[Dict[str, Any]] = []
        for name in self.servers:
            cached = self._tool_cache.get(name)
            if cached is not None:
                tools.extend(cached)
                continue
            try:
                client = await self._get_client(name)
                result = await client.list_tools()
                server_tools = [
                    {
                        "name": f"{name}__{tool.name}",
                        "description": tool.description or "",
                        "parameters": tool.input_schema or {"type": "object"},
                        "server": name,
                    }
                    for tool in result.tools
                ]
                self._tool_cache[name] = server_tools
                tools.extend(server_tools)
            except Exception as e:
                msg = f"Failed to list tools from server '{name}': {e}"
                logger.warning(msg)
                self._warnings.append(msg)
        return tools

    async def get_available_tools(self) -> List[Dict[str, Any]]:
        tools = await self._fetch_mcp_tool_definitions()
        if self._builtin_tools:
            tools.extend(self._builtin_tools.get_tool_definitions())
        return tools

    async def get_readonly_tools(self) -> List[Dict[str, Any]]:
        tools: List[Dict[str, Any]] = []
        if self._builtin_tools:
            tools.extend(self._builtin_tools.get_readonly_tool_definitions())
        return tools

    async def get_plan_tools(self) -> List[Dict[str, Any]]:
        tools = await self._fetch_mcp_tool_definitions()
        if self._builtin_tools:
            tools.extend(self._builtin_tools.get_plan_tool_definitions())
        return tools

    async def call_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        confirm_callback: Optional[Callable[[str], Any]] = None,
        permission_enforcer: Optional["ProfilePermissionEnforcer"] = None,
    ) -> str:
        server_name, sep, actual_name = tool_name.partition("__")
        if not server_name or not sep:
            return f"Error: Invalid tool name format: {tool_name}. Expected 'server__toolname'"
        # Check permissions for built-in tools
        if server_name == "builtin":
            if self._builtin_tools is None:
                return "Error: No built-in tools are registered"
            return await self._builtin_tools.call_tool(
                actual_name,
                arguments,
                confirm_callback=confirm_callback,
                permission_enforcer=permission_enforcer,
            )

        # TODO: add permission checks for external mcp servers.
        # Should be similar to the builtin tool manager and how
        # it calls tools with scoped permission checks.
        if server_name not in self.servers:
            return f"Error: Unknown server: {server_name}"

        client = await self._get_client(server_name)
        result = await client.call_tool(actual_name, arguments)
        text = "".join(c.text for c in result.content if hasattr(c, "text"))
        if not text and result.structured_content is not None:
            text = str(result.structured_content)
        if result.is_error:
            return f"Error: {text or result.content}"
        return text or str(result.content)

    def drain_builtin_attachments(self) -> tuple:
        """Return (attachments, caption) produced by the last builtin tool call."""
        if self._builtin_tools is None:
            return ([], "")
        return self._builtin_tools.drain_attachments()

    async def _get_client(self, name: str) -> Client:
        if name not in self._clients:
            config = self.servers[name]

            if config.transport == "http" and self._offline_mode:
                self._warnings.append(
                    f"MCP server '{name}' uses HTTP transport ({config.url}) "
                    f"but offline mode is enabled. Network may be unavailable."
                )

            if config.transport == "http":
                client = await self._exit_stack.enter_async_context(Client(config.url))
            else:
                params = StdioServerParameters(
                    command=config.command,
                    args=config.args,
                    env=config.env or None,
                )
                client = await self._exit_stack.enter_async_context(
                    Client(stdio_client(params))
                )

            self._clients[name] = client
        return self._clients[name]

    def pop_warnings(self) -> List[str]:
        warnings = list(self._warnings)
        self._warnings.clear()
        return warnings

    async def disconnect_all(self) -> None:
        try:
            await self._exit_stack.aclose()
        except (RuntimeError, BaseExceptionGroup):
            logger.debug(
                "Suppressed cancel scope error during MCP shutdown (known SDK issue)"
            )
        self._clients.clear()
        self._invalidate_tool_cache()

    def _load_config(self) -> None:
        path = Path(self.config_path)
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text())
            for entry in data:
                self.servers[entry["name"]] = MCPServerConfig(**entry)
        except Exception as e:
            msg = f"Failed to load MCP config: {e}"
            logger.warning(msg)
            self._warnings.append(msg)

    def _save_config(self) -> None:
        data = [asdict(cfg) for cfg in self.servers.values()]
        Path(self.config_path).write_text(json.dumps(data, indent=2))
