import json
import logging
from contextlib import AsyncExitStack

try:
    from exceptiongroup import BaseExceptionGroup
except ImportError:
    pass  # Python 3.11+ has BaseExceptionGroup as a builtin
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from mcp.client import Client
from mcp.client.stdio import stdio_client, StdioServerParameters

from .tools.manager import BuiltinToolManager
from .models import MCPServerConfig

logger = logging.getLogger(__name__)


class MCPClientManager:
    def __init__(
        self,
        config_path: str | None = None,
        builtin_tools: Optional["BuiltinToolManager"] = None,
        offline_mode: bool = True,
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
        Path(self.config_path).parent.mkdir(parents=True, exist_ok=True)
        self._load_config()
        # Cached MCP tool listings, keyed by server name. Populated on first
        # request in `get_available_tools`, cleared by add/remove/disconnect.
        self._tool_cache: Dict[str, List[Dict[str, Any]]] = {}

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
    ) -> str:
        server_name, sep, actual_name = tool_name.partition("__")
        if not server_name or not sep:
            return f"Error: Invalid tool name format: {tool_name}. Expected 'server__toolname'"
        if server_name == "builtin":
            if self._builtin_tools is None:
                return "Error: No built-in tools are registered"
            return await self._builtin_tools.call_tool(
                actual_name, arguments, confirm_callback=confirm_callback
            )
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
