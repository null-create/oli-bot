"""Agent harness — orchestrates the tool-calling loop and response streaming."""

import os
import re
import asyncio
import json
import yaml
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

from .backends import (
    Message,
    TextChunk,
    ToolCallChunk,
    ModelBackend,
    create_model_backend,
)
from .config import AppConfig, configs
from .mcp_client import MCPClientManager
from .profile_manager import ProfileData, ProfileManager
from .profiles.permissions import ProfilePermissionEnforcer
from .models import (
    ToolCallExecuting,
    ToolCallResult,
    AssistantResponse,
    Error,
    Done,
    StreamChunk,
    SubAgentRun,
    ThinkingChunk,
)

logger = logging.getLogger(__name__)

# Agent event types
AgentEvent = (
    ToolCallExecuting
    | ToolCallResult
    | AssistantResponse
    | StreamChunk
    | ThinkingChunk
    | Error
    | Done
)

PLAN_MODE_NOTE = (
    "You are in PLAN mode: only read-only research tools plus `notebook` and "
    "`todowrite` are available. Do not attempt to edit files or run destructive "
    "commands. Research the request as needed, then produce a complete, "
    "well-structured plan (goal, steps, files/areas affected, risks, "
    "verification). Once the plan is finalized, save it by calling `notebook` "
    "with action='set', page='plan-<short-kebab-case-slug>', and the full plan "
    "as content, then tell the user the exact page/path the tool reports back."
)


def sanitize_tool_history(messages: List[Message]) -> List[Message]:
    """Return a copy of ``messages`` with orphan tool_use/tool_result blocks removed.

    Backends that translate to Anthropic/Bedrock (visible as ``toolu_*`` /
    ``tooluse_*`` ids in [logs/backend.ndjson](logs/backend.ndjson)) reject any
    request whose messages contain an assistant ``tool_calls`` entry without a
    matching subsequent ``role=tool`` message, or a ``role=tool`` message whose
    ``tool_call_id`` has no preceding assistant that issued it. Auto-prune,
    cancellation, mode changes, and old sessions on disk can all leave the
    history in that state; this sanitizer repairs it just before send / on load.
    """
    kept: List[Message] = []
    i = 0
    n = len(messages)
    while i < n:
        m = messages[i]
        if m.role == "assistant" and m.tool_calls:
            expected_ids = [
                tc.get("id")
                for tc in m.tool_calls
                if isinstance(tc, dict) and tc.get("id")
            ]
            j = i + 1
            found_ids: set[str] = set()
            tool_msgs: List[Message] = []
            while j < n and messages[j].role == "tool":
                tool_msgs.append(messages[j])
                if messages[j].tool_call_id:
                    found_ids.add(messages[j].tool_call_id)
                j += 1
            missing = [tid for tid in expected_ids if tid not in found_ids]
            if missing:
                logger.warning(
                    "sanitize_tool_history: dropping assistant tool_calls with "
                    "unmatched ids %s and %d dangling tool msg(s)",
                    missing,
                    len(tool_msgs),
                )
                i = j
                continue
            kept.append(m)
            kept.extend(tool_msgs)
            i = j
            continue
        if m.role == "tool":
            logger.warning(
                "sanitize_tool_history: dropping orphan tool message "
                "(tool_call_id=%s)",
                m.tool_call_id,
            )
            i += 1
            continue
        kept.append(m)
        i += 1
    return kept


class Agent:
    def __init__(
        self,
        role: Optional[str],
        backend: ModelBackend,
        mcp_manager: MCPClientManager,
        mode: str = "agent",
        profile_manager: Optional[ProfileManager] = None,
        profile_name: str = "default",
        config: Optional[AppConfig] = None,
    ):
        """Initialize the Agent with the given backend and MCP manager."""
        self.config = config or AppConfig()
        self.role = role or ""
        self.backend = backend
        self.mcp_manager = mcp_manager
        self._mode = mode
        self._profile_manager = profile_manager or ProfileManager()
        self._profile_name = profile_name
        self._profile_data: ProfileData | None = None
        self._generating = False
        self._load_initial_profile()

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def profile_name(self) -> str:
        return self._profile_name

    @property
    def system_prompt(self) -> str:
        if self._profile_data is not None:
            return self._profile_data.system_prompt
        return ""

    @property
    def profile_data(self) -> ProfileData | None:
        return self._profile_data

    @property
    def permission_enforcer(self) -> ProfilePermissionEnforcer | None:
        if self._profile_data is not None:
            return self._profile_data.permission_enforcer
        return None

    @property
    def generating(self) -> bool:
        return self._generating

    def set_mode(self, mode: str) -> None:
        if mode not in ("ask", "agent", "chat", "plan"):
            raise ValueError(
                f"Invalid mode: {mode}. Must be 'ask', 'agent', 'chat', or 'plan'."
            )
        self._mode = mode

    def _load_initial_profile(self) -> None:
        try:
            self._profile_data = self._profile_manager.load_profile(self._profile_name)
        except ValueError:
            self._profile_name = "none"
            logger.debug(
                "Profile '%s' not found, starting without one", self._profile_name
            )

    def load_profile(self, name: str) -> ProfileData:
        self._profile_data = self._profile_manager.load_profile(name)
        self._profile_name = name
        return self._profile_data

    def list_profiles(self) -> List[str]:
        return self._profile_manager.list_profiles()

    def profile_exists(self, name: str) -> bool:
        return self._profile_manager.profile_exists(name)

    def create_profile(self, name: str, content: str) -> Path:
        return self._profile_manager.create_profile(name, content)

    async def process(
        self,
        messages: List[Message],
        tools: Optional[List[Dict[str, Any]]] = None,
        confirm_callback: Optional[Callable[[str], Any]] = None,
    ) -> AsyncIterator[AgentEvent]:
        self._generating = True
        try:
            working_messages = self._compose_messages(messages)
            # Must be captured before any yield: consumers may append their
            # own message (e.g. the final assistant reply) to ``messages``
            # the instant they observe a ``Done`` event, which would race
            # with a length check made after control returns to us.
            base_len = len(messages)

            if self._mode == "chat":
                async for event in self._final_stream(working_messages, tools):
                    yield event
                self._sync_back(messages, working_messages, base_len)
                return

            async for event in self._tool_loop(
                working_messages, tools, confirm_callback
            ):
                if isinstance(event, Done):
                    # Sync tool-call turns back before yielding Done, since
                    # the consumer may mutate ``messages`` as soon as it sees
                    # this event.
                    self._sync_back(messages, working_messages, base_len)
                    yield event
                    return
                yield event

            # Loop exhausted max_tool_iterations without an explicit Done.
            # Force a final text-only stream (no tools) so the user sees a
            # response instead of a silent hang.
            self._sync_back(messages, working_messages, base_len)
            async for event in self._final_stream(working_messages, tools):
                yield event
        finally:
            self._generating = False

    def _compose_messages(self, messages: List[Message]) -> List[Message]:
        """Return a shallow-copied message list with a system message that
        carries the current date. The caller's list is left untouched; only
        assistant/tool turns produced during ``_tool_loop`` are synced back.
        """
        composed = sanitize_tool_history(list(messages))
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S %Z")
        header = f"{self.system_prompt}\nCurrent date and time: {now}"
        if self._mode == "plan":
            header = f"{header}\n{PLAN_MODE_NOTE}"
        if composed and composed[0].role == "system":
            first = composed[0]
            composed[0] = Message(
                role=first.role,
                content=header,
                tool_calls=first.tool_calls,
                name=first.name,
                timestamp=first.timestamp,
                tool_call_id=first.tool_call_id,
            )
        else:
            composed.insert(0, Message(role="system", content=header))
        return composed

    @staticmethod
    def _sync_back(
        original: List[Message], working: List[Message], base_len: int
    ) -> None:
        """Append any assistant/tool turns produced during the run back
        onto the caller's list, without persisting our ephemeral system
        header. ``base_len`` must be the length of ``original`` captured
        before any events were yielded to the caller (see ``process``).
        """
        offset = (
            1
            if (
                working
                and working[0].role == "system"
                and (not original or original[0].role != "system")
            )
            else 0
        )
        for msg in working[base_len + offset :]:
            original.append(msg)

    async def _tool_loop(
        self,
        messages: List[Message],
        tools: Optional[List[Dict[str, Any]]],
        confirm_callback: Optional[Callable[[str], Any]],
    ) -> AsyncIterator[AgentEvent]:
        for _ in range(self.config.max_tool_iterations):
            stream = self.backend.stream_generate(
                messages, tools=tools if tools else []
            )

            full_response = ""
            tool_calls = []
            pending_images: List = []
            pending_caption: str = ""
            try:
                while True:
                    try:
                        event = await asyncio.wait_for(
                            stream.__anext__(),
                            timeout=self.config.stream_timeout,
                        )
                    except StopAsyncIteration:
                        break
                    if isinstance(event, TextChunk):
                        full_response += event.text
                        yield StreamChunk(event.text)
                    elif isinstance(event, ThinkingChunk):
                        yield event
                    elif isinstance(event, ToolCallChunk):
                        tool_calls = event.tool_calls
                        for tc in tool_calls:
                            logger.debug(
                                "Tool call assembled: id=%s name=%s parameters=%s",
                                tc.id,
                                tc.name,
                                tc.parameters,
                            )
            except asyncio.TimeoutError:
                err_text = (
                    f"Response timed out (no data for "
                    f"{self.config.stream_timeout:.0f} seconds)"
                )
                yield Error(err_text)
                # Emit an empty Done so the UI layer's "skip empty assistant
                # append" branch fires and error text never poisons history.
                yield Done(full_text="")
                return
            except Exception as e:
                msg = str(e)
                if "does not support tools" in msg:
                    err_text = (
                        f"Model '{self.backend.model}' does not support tool calling. "
                        f"Switch to a tool-capable model or use /mode chat."
                    )
                else:
                    err_text = f"Error: {msg}"
                logger.exception("Streaming failed in tool loop: %s", e)
                yield Error(err_text)
                yield Done(full_text="")
                return

            if not tool_calls:
                yield Done(full_text=full_response)
                return

            if full_response:
                yield AssistantResponse(full_response)

            tool_calls_dicts = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.parameters),
                    },
                }
                for tc in tool_calls
            ]
            messages.append(
                Message(
                    role="assistant",
                    content=full_response,
                    tool_calls=tool_calls_dicts,
                )
            )

            for tc in tool_calls:
                yield ToolCallExecuting(name=tc.name, parameters=tc.parameters)
                try:
                    result = await self.mcp_manager.call_tool(
                        tc.name,
                        tc.parameters,
                        confirm_callback=confirm_callback,
                    )
                except Exception as e:
                    result = f"Error: {e}"
                yield ToolCallResult(name=tc.name, result=result)
                messages.append(
                    Message(role="tool", content=result, tool_call_id=tc.id)
                )
                # Buffer image attachments until after all tool results land so
                # the assistant->tool block stays contiguous for sanitize_tool_history.
                drain = getattr(self.mcp_manager, "drain_builtin_attachments", None)
                if drain is not None:
                    atts, cap = drain()
                    if atts:
                        pending_images.extend(atts)
                        if cap and not pending_caption:
                            pending_caption = cap

            if pending_images:
                messages.append(
                    Message(
                        role="user",
                        content=pending_caption
                        or "[Attached image(s) from view_image tool]",
                        images=pending_images,
                    )
                )

    async def _final_stream(
        self,
        messages: List[Message],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> AsyncIterator[AgentEvent]:
        full_response = ""
        errored = False
        try:
            # Forward tools so backends that require a matching tool schema
            # for any toolUse/toolResult blocks in history (e.g. Bedrock via
            # OpenAI-compatible proxies) don't 400. ToolCallChunks from this
            # pass are ignored below — only text/thinking is consumed.
            stream = self.backend.stream_generate(messages, tools=tools)
            while True:
                try:
                    event = await asyncio.wait_for(
                        stream.__anext__(), timeout=self.config.stream_timeout
                    )
                except StopAsyncIteration:
                    break
                if isinstance(event, TextChunk):
                    full_response += event.text
                    yield StreamChunk(event.text)
                elif isinstance(event, ThinkingChunk):
                    yield event
        except asyncio.TimeoutError:
            err_text = (
                f"Response timed out (no data for "
                f"{self.config.stream_timeout:.0f} seconds)"
            )
            errored = True
            yield Error(err_text)
        except Exception as e:
            err_text = f"Error: {e}"
            logger.exception("Streaming failed: %s", e)
            errored = True
            yield Error(err_text)
        finally:
            if errored:
                # Keep error text out of persisted history; the Error event
                # already drove the red UI panel.
                yield Done(full_text="")
            else:
                if not full_response:
                    full_response = "The model completed all tool calls but did not produce a final summary."
                yield Done(full_text=full_response)


_ROOT_AGENT_NAMES = {"root-agent", "root"}


def _expand_env(
    value: Optional[str],
    extra_env: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    """Expand ``${VAR}`` / ``$VAR`` references against the process environment.

    Resolution order:
      1. ``os.path.expandvars`` — covers real shell env vars (``os.environ``).
      2. ``extra_env`` fallback — covers vars sourced from ``.env`` via
         pydantic-settings, which populates ``AppConfig`` fields but does
         **not** inject into ``os.environ``.  Pass a dict keyed by the
         ``OLI_``-prefixed env-var name (e.g. ``{"OLI_OPENAI_API_KEY": key}``).

    Returns ``None`` unchanged so optional config fields stay optional.
    Any reference that cannot be resolved expands to an empty string and the
    caller falls through to the backend's own default.
    """
    if not value:
        return value
    expanded = os.path.expandvars(value)
    # If os.path.expandvars left ${...} tokens unresolved (because the var is
    # only in .env / pydantic-settings, not in os.environ), substitute from
    # extra_env before giving up.
    if extra_env and "${" in expanded:
        expanded = re.sub(
            r"\$\{([^}]+)\}",
            lambda m: extra_env.get(m.group(1), m.group(0)),
            expanded,
        )
    return expanded or None


async def stream_sub_agent_run(
    run: SubAgentRun,
    events: AsyncIterator[AgentEvent],
) -> str:
    """Consume a sub-agent's event stream into a ``SubAgentRun``.

    Every event is appended to ``run.events`` in arrival order so the TUI can
    render the work live; ``run.activity`` / ``run.status`` track progress.
    Returns the final assistant text (``Done.full_text``), mirroring the
    previous dispatch contract of ``Agent.process()``.
    """
    full_text = ""
    async for event in events:
        run.events.append(event)
        if isinstance(event, StreamChunk):
            run.activity = "streaming..."
        elif isinstance(event, ThinkingChunk):
            run.activity = "thinking..."
        elif isinstance(event, AssistantResponse):
            run.activity = "streaming..."
        elif isinstance(event, ToolCallExecuting):
            run.activity = f"calling {event.name}"
        elif isinstance(event, ToolCallResult):
            run.activity = f"tool result: {event.name}"
        elif isinstance(event, Error):
            run.status = "error"
            run.activity = f"error: {event.message}"
        elif isinstance(event, Done):
            run.status = "done"
            run.activity = "done"
            run.full_text = event.full_text
            full_text = event.full_text
    return full_text


class AgentPool:
    """Pool for sub agents to be selected from at runtime by the Root Agent"""

    def __init__(self, mcp_manager: MCPClientManager):
        self.agent_pool: dict[str, dict[str, Agent]] = {}
        self.mcp_manager = mcp_manager
        self._build_agent_pool()

    def select_agent(self, agent_pool_name: str, agent_name: str) -> Agent:
        if agent_pool_name not in self.agent_pool:
            raise ValueError(f"{agent_pool_name} not found in agent pool")
        if agent_name not in self.agent_pool[agent_pool_name]:
            raise ValueError(
                f"{agent_name} not found in agent pool '{agent_pool_name}'"
            )

        return self.agent_pool[agent_pool_name][agent_name]

    def list_agents(self, agent_pool_name: str) -> List[str]:
        """Return the delegate-able agent names in a pool (root agent excluded)."""
        return list(self.agent_pool.get(agent_pool_name, {}).keys())

    def _build_agent_pool(self) -> None:
        configs_file = os.path.join(
            os.path.abspath(os.path.dirname(__file__)), "agents.yaml"
        )
        if not os.path.exists(configs_file):
            return  # No agents.yaml file found, skip building the agent pool

        try:
            with open(configs_file, "r") as f:
                agent_pool_config: dict = yaml.safe_load(f)
        except Exception as e:
            logger.error(f"Failed to load agent pool configuration: {e}")
            return

        agent_pools = agent_pool_config.get("agent-pools", [])

        for agent_pool in agent_pools:
            pool_name = agent_pool.get("name", "default")
            agent_configs = agent_pool.get("agents", [])
            if len(agent_configs) == 0 or len(agent_configs) > configs.agent_pool_size:
                raise ValueError(
                    f"Agent pool '{pool_name}' has {len(agent_configs)} agents. "
                    f"Expected between 1 and {configs.agent_pool_size} agents."
                )

            for agent_config in agent_configs:
                role = agent_config.get("name")
                if not role:
                    logger.warning(
                        "Skipping agent config with no name: %s", agent_config
                    )
                    continue
                if role in _ROOT_AGENT_NAMES:
                    logger.debug(
                        "Skipping '%s' — root agent is not a delegate target", role
                    )
                    continue

                backend_cfg = agent_config.get("backend", {}) or {}
                backend_type = backend_cfg.get("type")

                # Build a fallback env dict from pydantic-settings so that
                # ${VAR} tokens in agents.yaml resolve even when the var lives
                # only in .env (not exported into os.environ).
                extra_env: Dict[str, str] = {
                    f"OLI_{name.upper()}": str(val)
                    for name, val in configs.model_dump().items()
                    if val  # skip empty / falsy values
                }

                model = _expand_env(agent_config.get("model"), extra_env)
                if not model or not backend_type:
                    logger.warning(
                        "Skipping invalid agent configuration for '%s': %s",
                        role,
                        agent_config,
                    )
                    continue

                backend_url = _expand_env(backend_cfg.get("base_url"), extra_env)
                backend_api_key = _expand_env(backend_cfg.get("api_key"), extra_env)

                try:
                    agent = Agent(
                        role=role,
                        backend=create_model_backend(
                            url=backend_url,
                            backend_type=backend_type,
                            model=model,
                            api_key=backend_api_key,
                            base_url=backend_url,
                        ),
                        mcp_manager=self.mcp_manager,
                        config=configs,
                    )
                except Exception as e:
                    logger.error("Failed to build agent '%s': %s", role, e)
                    continue

                if pool_name not in self.agent_pool:
                    self.agent_pool[pool_name] = {}
                self.agent_pool[pool_name][role] = agent
