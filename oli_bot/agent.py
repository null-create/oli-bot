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
from .profiles.manager import ProfileData, ProfileManager
from .profiles.permissions import ProfilePermissionEnforcer
from .models import (
    ToolCallExecuting,
    ToolCallResult,
    AssistantResponse,
    Error,
    Done,
    StreamChunk,
    SubAgentCompleted,
    SubAgentEvent,
    SubAgentProgress,
    SubAgentRun,
    SubAgentStarted,
    ThinkingChunk,
    Usage,
    UsageChunk,
    UsageEvent,
)
from .tools.memory import _current_sub_run

logger = logging.getLogger(__name__)

# Agent event types
AgentEvent = (
    ToolCallExecuting
    | ToolCallResult
    | AssistantResponse
    | StreamChunk
    | ThinkingChunk
    | UsageEvent
    | Error
    | Done
    | SubAgentStarted
    | SubAgentProgress
    | SubAgentCompleted
    | SubAgentEvent
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


def _merge_usage(acc: Usage, u: Usage) -> Usage:
    """Sum per-call usage into a cumulative run total (flag estimated if any)."""
    return Usage(
        prompt_tokens=acc.prompt_tokens + u.prompt_tokens,
        completion_tokens=acc.completion_tokens + u.completion_tokens,
        estimated=acc.estimated or u.estimated,
    )


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
        run_usage = Usage()
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
                    elif isinstance(event, UsageChunk):
                        run_usage = _merge_usage(run_usage, event.usage)
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
                if run_usage.total_tokens:
                    yield UsageEvent(usage=run_usage)
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
                    if tc.name == "builtin__dispatch":
                        result_holder: List[str] = [""]
                        async for _ev in self._run_dispatch_with_events(
                            tc.name,
                            tc.parameters,
                            confirm_callback,
                            result_holder,
                        ):
                            yield _ev
                        result = result_holder[0]
                    else:
                        result = await self.mcp_manager.call_tool(
                            tc.name,
                            tc.parameters,
                            confirm_callback=confirm_callback,
                            permission_enforcer=self.permission_enforcer,
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

    async def _run_dispatch_with_events(
        self,
        name: str,
        parameters: Dict[str, Any],
        confirm_callback: Optional[Callable[[str], Any]],
        result_holder: List[str],
    ) -> AsyncIterator[AgentEvent]:
        """Execute a ``builtin__dispatch`` tool call while streaming sub-agent
        events through the root agent's event stream.

        The dispatch handler pushes ``SubAgent*`` lifecycle events (and wrapped
        inner events) onto ``mcp_manager.sub_agent_queue`` as the concurrent
        fan-out progresses. This helper runs the tool call as a background
        task and alternately drains the queue, yielding each event so the
        caller (the tool loop) relays it live to the consumer. When the tool
        task finishes, the dispatch's aggregated result string is stored in
        ``result_holder[0]``.
        """
        queue: asyncio.Queue = asyncio.Queue()
        self.mcp_manager.sub_agent_queue = queue
        task = asyncio.create_task(
            self.mcp_manager.call_tool(
                name,
                parameters,
                confirm_callback=confirm_callback,
                permission_enforcer=self.permission_enforcer,
            )
        )
        try:
            while not task.done() or not queue.empty():
                getter = asyncio.create_task(queue.get())
                done, _ = await asyncio.wait(
                    {task, getter}, return_when=asyncio.FIRST_COMPLETED
                )
                if getter in done:
                    event = getter.result()
                    if isinstance(
                        event,
                        (
                            SubAgentStarted,
                            SubAgentProgress,
                            SubAgentCompleted,
                            SubAgentEvent,
                        ),
                    ):
                        yield event
                else:
                    getter.cancel()
        finally:
            self.mcp_manager.sub_agent_queue = None
        result_holder[0] = task.result()

    async def _final_stream(
        self,
        messages: List[Message],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> AsyncIterator[AgentEvent]:
        full_response = ""
        errored = False
        usage = Usage()
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
                elif isinstance(event, UsageChunk):
                    usage = _merge_usage(usage, event.usage)
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
                if usage.total_tokens:
                    yield UsageEvent(usage=usage)
                yield Done(full_text=full_response)


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
    event_sink: Optional["asyncio.Queue"] = None,
) -> str:
    """Consume a sub-agent's event stream into a ``SubAgentRun``.

    Every event is appended to ``run.events`` in arrival order so the TUI can
    render the work live; ``run.activity`` / ``run.status`` track progress.
    When ``event_sink`` is provided, sub-agent lifecycle and inner events are
    pushed onto the queue so an outer consumer (e.g. the API WebSocket relay)
    can stream them live alongside the root agent's own events.
    Returns the final assistant text (``Done.full_text``), mirroring the
    previous dispatch contract of ``Agent.process()``.
    """

    async def emit(event: AgentEvent) -> None:
        if event_sink is not None:
            await event_sink.put(event)

    await emit(
        SubAgentStarted(
            task_id=run.task_id,
            agent_name=run.agent_name,
            pool_name=run.pool_name,
            task=run.task,
        )
    )

    full_text = ""
    async for event in events:
        run.events.append(event)
        if event_sink is not None:
            await emit(
                SubAgentEvent(
                    task_id=run.task_id,
                    agent_name=run.agent_name,
                    event=event,
                )
            )
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
        await emit(
            SubAgentProgress(
                task_id=run.task_id,
                agent_name=run.agent_name,
                activity=run.activity,
                status=run.status,
            )
        )

    await emit(
        SubAgentCompleted(
            task_id=run.task_id,
            agent_name=run.agent_name,
            status=run.status,
            full_text=run.full_text,
        )
    )
    return full_text


async def run_agent_dispatch(
    pool: "AgentPool",
    runs: List[SubAgentRun],
    tools: Optional[List[Dict[str, Any]]],
    confirm_callback: Callable[[str], Any],
    event_sink: Optional["asyncio.Queue"] = None,
) -> str:
    """Fan a batch of sub-agent runs out concurrently, aggregating results.

    ``runs`` must already be populated with ``SubAgentRun`` objects (the
    caller keeps a reference for its own UI tracking). Each run consumes its
    sub-agent's event stream via ``stream_sub_agent_run``; when ``event_sink``
    is given, sub-agent lifecycle events are pushed onto it as they happen.
    Returns the aggregated ``## <agent>\\n<result>`` block used as the
    ``dispatch`` tool result.
    """

    async def run_task(idx: int) -> tuple[str, str]:
        run = runs[idx]
        # Tag the asyncio task context so _todowrite_handler knows which
        # sub-agent run is active. asyncio.gather copies the context into each
        # spawned Task, so concurrent sub-agents stay isolated.
        _current_sub_run.set(run)
        try:
            sub_agent = pool.select_agent(run.pool_name, run.agent_name)
            sub_messages = [Message(role="user", content=run.task)]
            result = await stream_sub_agent_run(
                run,
                sub_agent.process(
                    sub_messages,
                    tools=tools,
                    confirm_callback=confirm_callback,
                ),
                event_sink=event_sink,
            )
            return run.agent_name, result
        except Exception as e:
            logger.exception("Dispatched agent '%s' failed", run.agent_name)
            run.status = "error"
            run.activity = f"error: {e}"
            if event_sink is not None:
                await event_sink.put(
                    SubAgentProgress(
                        task_id=run.task_id,
                        agent_name=run.agent_name,
                        activity=run.activity,
                        status="error",
                    )
                )
                await event_sink.put(
                    SubAgentCompleted(
                        task_id=run.task_id,
                        agent_name=run.agent_name,
                        status="error",
                        full_text="",
                    )
                )
            return run.agent_name, f"Error: {e}"

    results = await asyncio.gather(*(run_task(i) for i in range(len(runs))))
    return "\n\n".join(f"## {name}\n{text}" for name, text in results)


def register_dispatch_tool(
    builtin_tools: "BuiltinToolManager",
    pool: "AgentPool",
    handler: Callable[..., str],
) -> None:
    """Register the `dispatch` built-in tool that fans a batch of tasks out
    to pooled sub-agents concurrently.

    When multiple pools are defined the tool schema gains an optional ``pool``
    field on each task item so the root agent can target any pool by name.
    Single-pool configurations keep the simpler schema unchanged.
    """
    # Collect agents per pool, preserving definition order.
    all_pool_names: list[str] = list(pool.agent_pool.keys())
    pool_agent_map: dict[str, list[str]] = {
        p: pool.list_agents(p) for p in all_pool_names
    }
    all_agent_names: list[str] = [
        name for names in pool_agent_map.values() for name in names
    ]

    if not all_agent_names:
        logger.warning(
            "Agent pool has no delegate-able agents; 'dispatch' tool not registered"
        )
        return

    has_multiple_pools = len(all_pool_names) > 1

    # Human-readable summary used in descriptions, e.g.:
    #   "default: researcher, analyst-agent; coding: code-writer"
    pool_summary = "; ".join(
        f"{p}: {', '.join(agents)}" for p, agents in pool_agent_map.items() if agents
    )

    tool_description = (
        "Dispatch one or more tasks to specialist sub-agents to run "
        "CONCURRENTLY (in parallel, not sequentially). Use this instead "
        "of calling sub-agents one at a time. "
        + (
            f"Available agents per pool — {pool_summary}"
            if has_multiple_pools
            else f"Available agents: {', '.join(all_agent_names)}"
        )
    )

    agent_description = "Name of the sub-agent to run this task. " + (
        f"Each agent belongs to a specific pool — {pool_summary}."
        if has_multiple_pools
        else f"Available: {', '.join(all_agent_names)}."
    )

    task_item_properties: dict = {
        "agent": {
            "type": "string",
            "enum": all_agent_names,
            "description": agent_description,
        },
        "task": {
            "type": "string",
            "description": "The task/instructions for this agent.",
        },
    }

    # Only expose `pool` in the schema when multiple pools are configured —
    # keeps the single-pool case clean and backwards-compatible.
    if has_multiple_pools:
        task_item_properties["pool"] = {
            "type": "string",
            "enum": all_pool_names,
            "description": (
                f"The agent pool to target. Available pools: {', '.join(all_pool_names)}. "
                "Defaults to 'default' if omitted."
            ),
        }

    builtin_tools.register_tool(
        name="dispatch",
        description=tool_description,
        parameters={
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "description": "The batch of tasks to run in parallel.",
                    "items": {
                        "type": "object",
                        "properties": task_item_properties,
                        "required": ["agent", "task"],
                    },
                },
            },
            "required": ["tasks"],
        },
        handler=handler,
    )


class AgentPool:
    """Pool for sub agents to be selected from at runtime by the Root Agent"""

    def __init__(
        self,
        mcp_manager: MCPClientManager,
        config: Optional[AppConfig] = None,
    ):
        self.agent_pool: dict[str, dict[str, Agent]] = {}
        self.mcp_manager = mcp_manager
        self._config = config or configs
        self._build_agent_pools()

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

    def has_agents(self) -> bool:
        """True when at least one delegate-able agent is loaded (any pool)."""
        return any(names for names in self.agent_pool.values())

    def _override_env(self) -> str:
        """Resolve an explicit agents.yaml path from the config/env layers.

        Precedence: a real ``OLI_AGENTS_YAML`` env var beats the resolved
        ``AppConfig.agents_yaml`` value (which itself covers the ``OLI_``
        env or a ``.env`` line via pydantic-settings, plus ``settings.json``
        via ``SettingsManager``). The module ``configs`` singleton is a final
        fallback so chat/api config instances built with ``_env_file=None``
        still honour a repo-root ``.env``.
        """
        return (
            os.environ.get("OLI_AGENTS_YAML")
            or self._config.agents_yaml
            or configs.agents_yaml
            or ""
        )

    def _resolve_agents_config_path(self) -> Optional[str]:
        """Locate the ``agents.yaml`` config, or return None.

        Candidate locations, tried in order:
          1. ``$OLI_AGENTS_YAML`` / ``OLI_AGENTS_YAML`` in ``.env`` /
             ``settings.json`` ``model_params.agents_yaml`` — explicit override.
          2. ``<package_dir>/agents.yaml`` — beside ``agent.py`` (default when
             installed as a real wheel with the file shipped as package data).
          3. ``<repo_root>/agents.yaml`` — one level above the package (local
             source checkouts keep it next to ``pyproject.toml``).
          4. ``<cwd>/agents.yaml`` — the current working directory, so a
             project-local pool can be picked up just by launching ``oli``
             from the repo root (no env var required).
          5. ``~/.config/oli/agents.yaml`` — user config dir.
        The first path that exists is returned.
        """
        candidates: List[str] = []
        override = self._override_env()
        if override:
            candidates.append(override)
        package_dir = os.path.abspath(os.path.dirname(__file__))
        candidates.append(os.path.join(package_dir, "agents.yaml"))
        candidates.append(os.path.join(os.path.dirname(package_dir), "agents.yaml"))
        candidates.append(os.path.join(package_dir, "oli_bot", "agents.yaml"))
        candidates.append(str(Path.cwd() / "agents.yaml"))
        candidates.append(os.path.join(Path.home(), ".config", "oli", "agents.yaml"))
        for candidate in candidates:
            if os.path.exists(candidate):
                return candidate
        return None

    def _build_agent_pools(self) -> None:
        configs_file = self._resolve_agents_config_path()
        if not configs_file:
            # Loud, not silent: pooling was explicitly enabled, so an empty
            # pool must be surfaced rather than quietly skipped.
            logger.error(
                "No agents.yaml found for agent pooling. Checked $OLI_AGENTS_YAML, "
                "the package dir, package/oli_bot, the repo root, the current "
                "working directory, and ~/.config/oli. The 'dispatch' tool will "
                "not be available."
            )
            return  # No agents.yaml file found, skip building the agent pool

        logger.debug("Loading agent pool configuration from %s", configs_file)

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
            if (
                len(agent_configs) == 0
                or len(agent_configs) > self._config.agent_pool_size
            ):
                raise ValueError(
                    f"Agent pool '{pool_name}' has {len(agent_configs)} agents. "
                    f"Expected between 1 and {self._config.agent_pool_size} agents."
                )

            for agent_config in agent_configs:
                role = agent_config.get("name")
                if not role:
                    logger.warning(
                        "Skipping agent config with no name: %s", agent_config
                    )
                    continue
                if role in ("root-agent", "root"):
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

                profile_name = agent_config.get("profile") or "default"
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
                        profile_name=profile_name,
                    )
                except Exception as e:
                    logger.error("Failed to build agent '%s': %s", role, e)
                    continue

                if pool_name not in self.agent_pool:
                    self.agent_pool[pool_name] = {}
                if role in self.agent_pool[pool_name]:
                    # Silently overwriting a delegate with a same-named later
                    # entry makes pool confusion easy to miss — surface it.
                    logger.warning(
                        "Duplicate agent name '%s' in pool '%s': '%s' is being "
                        "overwritten by a later entry in agents.yaml.",
                        role,
                        pool_name,
                        role,
                    )
                self.agent_pool[pool_name][role] = agent
