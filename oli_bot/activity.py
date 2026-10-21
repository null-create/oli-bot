"""Turn activity capture — records *what happened* during an agent turn.

The persisted transcript only says *what was said*: the assistant text, the
tool-call requests and the tool results. The model reasoning, the per-call
timings and the dispatched sub-agent runs are not part of the message list
because they are not part of the conversation the model sees.

This module records them so a reopened session can rebuild the same view the
live UI showed. The result is serialized onto the message that closes a turn
(see ``Message.activity``) and read back by clients; it is never sent to the
model, because backends build their provider payload field by field.
"""

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .models import (
    Message,
    StreamChunk,
    SubAgentCompleted,
    SubAgentEvent,
    SubAgentProgress,
    SubAgentStarted,
    ToolCallExecuting,
    ToolCallResult,
)


@dataclass
class ActivityToolCall:
    """One tool invocation performed during a turn."""

    id: str
    name: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    result: Optional[str] = None
    elapsed: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "parameters": self.parameters,
            "result": self.result,
            "elapsed": self.elapsed,
        }


@dataclass
class SubAgentRecord:
    """A dispatched sub-agent run and everything it produced."""

    task_id: str
    agent_name: str
    pool_name: str = "default"
    task: str = ""
    status: str = "running"
    activity: str = ""
    full_text: str = ""
    text: str = ""
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    todos: List[dict] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "agent_name": self.agent_name,
            "pool_name": self.pool_name,
            "task": self.task,
            "status": self.status,
            "activity": self.activity,
            "full_text": self.full_text,
            "text": self.text,
            "tool_calls": self.tool_calls,
            "todos": self.todos,
        }


class TurnActivity:
    """Accumulates the activity of a single agent turn.

    Thinking text is tracked as *segments* — a segment ends when the model
    switches to text or to a tool call — and the segments are joined with a
    blank line, mirroring how the clients group streamed thinking blocks.
    """

    def __init__(self) -> None:
        self._started = time.perf_counter()
        self._thinking_segment = ""
        self._thinking_segments: List[str] = []
        self._tool_calls: List[ActivityToolCall] = []
        self._tool_calls_by_id: Dict[str, ActivityToolCall] = {}
        self._sub_agents: List[SubAgentRecord] = []
        self._sub_agents_by_id: Dict[str, SubAgentRecord] = {}

    # ── thinking ──────────────────────────────────────────────────────

    def add_thinking(self, text: str) -> None:
        self._thinking_segment += text

    def _close_thinking_segment(self) -> None:
        if self._thinking_segment:
            self._thinking_segments.append(self._thinking_segment)
            self._thinking_segment = ""

    def close_thinking_segment(self) -> None:
        """End the current thinking segment (the model changed mode)."""
        self._close_thinking_segment()

    @property
    def thinking(self) -> str:
        segments = list(self._thinking_segments)
        if self._thinking_segment:
            segments.append(self._thinking_segment)
        return "\n\n".join(s.strip() for s in segments if s.strip())

    # ── tool calls ────────────────────────────────────────────────────

    def start_tool_call(
        self, call_id: str, name: str, parameters: Dict[str, Any]
    ) -> None:
        call = ActivityToolCall(id=call_id, name=name, parameters=parameters or {})
        self._tool_calls.append(call)
        if call_id:
            self._tool_calls_by_id[call_id] = call

    def finish_tool_call(self, call_id: str, result: str, elapsed: float) -> None:
        call = self._tool_calls_by_id.get(call_id)
        if call is None:
            # Fall back to the most recent call still awaiting a result.
            for candidate in reversed(self._tool_calls):
                if candidate.result is None:
                    call = candidate
                    break
        if call is None:
            return
        call.result = result
        call.elapsed = elapsed

    @property
    def tool_calls(self) -> List[ActivityToolCall]:
        return self._tool_calls

    # ── sub-agents ────────────────────────────────────────────────────

    def observe(self, event: Any) -> None:
        """Fold a sub-agent lifecycle event into the activity."""
        if isinstance(event, SubAgentStarted):
            record = SubAgentRecord(
                task_id=event.task_id,
                agent_name=event.agent_name,
                pool_name=event.pool_name,
                task=event.task,
                status="running",
            )
            self._sub_agents.append(record)
            self._sub_agents_by_id[event.task_id] = record
            return
        if isinstance(event, SubAgentEvent):
            record = self._sub_agents_by_id.get(event.task_id)
            if record is not None:
                self._observe_inner(record, event.event)
            return
        if isinstance(event, SubAgentProgress):
            record = self._sub_agents_by_id.get(event.task_id)
            if record is not None:
                record.activity = event.activity
                record.status = event.status
            return
        if isinstance(event, SubAgentCompleted):
            record = self._sub_agents_by_id.get(event.task_id)
            if record is not None:
                record.status = event.status
                record.activity = event.status
                record.full_text = event.full_text
                if event.todos:
                    record.todos = list(event.todos)
            return

    @staticmethod
    def _observe_inner(record: SubAgentRecord, event: Any) -> None:
        if isinstance(event, StreamChunk):
            record.text += event.text
        elif isinstance(event, ToolCallExecuting):
            record.tool_calls.append(
                {"name": event.name, "parameters": event.parameters or {}}
            )
        elif isinstance(event, ToolCallResult):
            for call in record.tool_calls:
                if "result" not in call:
                    call["result"] = event.result
                    break

    @property
    def sub_agents(self) -> List[SubAgentRecord]:
        return self._sub_agents

    # ── serialization ─────────────────────────────────────────────────

    def elapsed(self) -> float:
        return time.perf_counter() - self._started

    def is_empty(self) -> bool:
        return not (self.thinking or self._tool_calls or self._sub_agents)

    def to_dict(self, elapsed: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """Serialize the activity, or ``None`` when there is nothing to record."""
        if self.is_empty():
            return None
        return {
            "thinking": self.thinking,
            "elapsed": round(self.elapsed() if elapsed is None else elapsed, 3),
            "tool_calls": [call.to_dict() for call in self._tool_calls],
            "sub_agents": [run.to_dict() for run in self._sub_agents],
        }


def close_thinking_segment(activity: TurnActivity) -> None:
    """End the current thinking segment (called when the model changes mode)."""
    activity.close_thinking_segment()


def attach_turn_activity(
    messages: List[Message], turn_start: int, activity: Optional[Dict[str, Any]]
) -> bool:
    """Attach a turn's activity to the message that closes the turn.

    The activity rides on an existing message instead of a dedicated one
    because an empty assistant message would be rejected by strict provider
    validators when the conversation is replayed. The last assistant-or-tool
    message of the turn is chosen: the final assistant reply when the turn
    produced one, otherwise the last tool result. Returns whether anything
    was attached.
    """
    if not activity:
        return False
    for message in reversed(messages[turn_start:]):
        if message.role in ("assistant", "tool"):
            message.activity = activity
            return True
    return False


__all__ = [
    "ActivityToolCall",
    "SubAgentRecord",
    "TurnActivity",
    "attach_turn_activity",
]
