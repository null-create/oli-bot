from enum import Enum

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from .profiles.schema import ProfileManifest
from .profiles.permissions import ProfilePermissionEnforcer


@dataclass
class ImageAttachment:
    """In-memory image attached to a Message. Not persisted to session JSON."""

    data: bytes
    media_type: str
    source_description: str = ""
    width: Optional[int] = None
    height: Optional[int] = None


@dataclass
class Message:
    role: str
    content: str
    tool_calls: Optional[List[Dict]] = None
    name: Optional[str] = None
    timestamp: Optional[str] = None
    tool_call_id: Optional[str] = None
    images: Optional[List[ImageAttachment]] = None


@dataclass
class ToolCall:
    id: str = ""
    name: str = ""
    description: str = ""
    parameters: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ModelResponse:
    content: str
    tool_calls: Optional[List[ToolCall]] = None
    finish_reason: str = "stop"
    error: str = ""


@dataclass
class TextChunk:
    text: str


@dataclass
class ThinkingChunk:
    text: str


@dataclass
class MCPServerConfig:
    name: str
    transport: str = "stdio"
    command: str = ""
    args: List[str] = field(default_factory=list)
    env: Optional[Dict[str, str]] = None
    url: str = ""


@dataclass
class ToolCallChunk:
    tool_calls: List[ToolCall]


@dataclass
class ToolCallExecuting:
    name: str
    parameters: Dict[str, Any]


@dataclass
class ToolCallResult:
    name: str
    result: str


@dataclass
class AssistantResponse:
    content: str


@dataclass
class StreamChunk:
    text: str


@dataclass
class Error:
    message: str


@dataclass
class Done:
    full_text: str


@dataclass
class ProfileData:
    system_prompt: str
    manifest: ProfileManifest
    permission_enforcer: ProfilePermissionEnforcer


@dataclass
class HostConfig:
    name: str
    url: str
    active: bool = False
    default_model: Optional[str] = None
    large_model: Optional[str] = None
    small_model: Optional[str] = None
    registered_models: Dict[str, str] = field(default_factory=dict)


@dataclass
class TodoItem:
    """Represents a single todo item with content, status, and priority."""

    content: str
    status: str  # pending, in_progress, completed, cancelled
    priority: str  # high, medium, low


@dataclass
class TodoListState:
    """Current state of the agent's todo list with computed counts.

    Used for rendering the todo widget in the TUI. Provides a clean snapshot
    of all items and their status breakdown.
    """

    items: List[TodoItem] = field(default_factory=list)
    last_updated: str = ""
    total_count: int = 0
    pending_count: int = 0
    in_progress_count: int = 0
    completed_count: int = 0
    cancelled_count: int = 0


@dataclass
class SubAgentRun:
    """Live record of a single dispatched sub-agent run.

    ``events`` collects every ``AgentEvent`` yielded by the sub-agent's
    ``Agent.process()`` in arrival order so the TUI can replay / render the
    work in real time. ``status`` is one of ``running``, ``done``, ``error``.
    ``todos`` holds the most recent todo list written by the sub-agent via
    ``builtin__todowrite``; the tree node UI reflects this in real time.
    """

    task_id: str
    agent_name: str
    task: str
    pool_name: str = "default"
    status: str = "running"
    activity: str = ""
    events: List[Any] = field(default_factory=list)
    full_text: str = ""
    started_at: str = ""
    todos: List[dict] = field(default_factory=list)


class AgentRole(Enum):
    SEARCH = "search"
    READ = "read"
    WRITE = "write"
    PLAN = "plan"


class ChatCompletionMessage(BaseModel):
    role: str
    content: Any = ""
    name: Optional[str] = None


class ChatCompletionRequest(BaseModel):
    model: Optional[str] = None
    messages: List[ChatCompletionMessage]
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    max_completion_tokens: Optional[int] = None
    stream: Optional[bool] = False
    top_p: Optional[float] = None
    n: Optional[int] = None
    stop: Optional[Any] = None
