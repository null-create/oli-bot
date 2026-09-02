"""Unified permission gate for built-in tools.

Collapses the five independent enforcement layers that used to live inline in
``BuiltinToolManager.call_tool`` (profile allow/deny, session workspace scope +
sensitive-file detection, offline-mode network gating, dry-run destructive
gating, unknown-tool rejection) behind a single ``PermissionGate.evaluate`` call
that returns a ``PermissionDecision``.

The individual policy sources (``ProfilePermissionEnforcer``,
``Session.needs_permission``, ``AppConfig``, and the ``DESTRUCTIVE_TOOLS`` /
``NETWORK_TOOLS`` categorization sets) are unchanged — only the *dispatch* is
unified. Message text is preserved verbatim so existing tests remain green.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, Iterable, Literal, Optional

if TYPE_CHECKING:
    from ..config import AppConfig
    from ..profiles.permissions import ProfilePermissionEnforcer
    from ..sessions import Session

logger = logging.getLogger(__name__)


Outcome = Literal["allow", "deny", "prompt", "preview"]


@dataclass(frozen=True)
class PermissionDecision:
    """Single decision object returned by ``PermissionGate.evaluate``.

    - ``outcome`` — what the caller should do
    - ``reason`` — human-readable reason (used verbatim in error strings)
    - ``scope`` — for ``outcome="prompt"``: the session scope name to grant on ``"session"``
    - ``description`` — for ``outcome="prompt"``: string shown to the user in the modal
    - ``preview`` — for ``outcome="preview"``: string returned to the model in place of the tool result
    - ``source`` — which gate produced the decision, for audit/logging
    """

    outcome: Outcome
    reason: str = ""
    scope: Optional[str] = None
    description: str = ""
    preview: str = ""
    source: str = ""


class PermissionGate:
    """Runs the five gates in a fixed order and returns a ``PermissionDecision``.

    Gate order (identical to the pre-refactor inline sequence in
    ``BuiltinToolManager.call_tool``):

    1. Unknown tool         → ``deny`` (source ``"registry"``)
    2. Profile enforcer     → ``deny`` (source ``"profile"``)
    3. Session workspace/sensitive → ``prompt`` (source ``"session"``)
    4. Offline + network    → ``deny`` (source ``"offline"``)
    5. Dry-run + destructive → ``preview`` (source ``"dry_run"``)
    6. Otherwise            → ``allow``
    """

    def __init__(
        self,
        session: Optional["Session"],
        config: "AppConfig",
        permission_enforcer: Optional["ProfilePermissionEnforcer"],
        known_tools: Iterable[str],
        destructive_tools: Iterable[str],
        network_tools: Iterable[str],
    ) -> None:
        self._session = session
        self._config = config
        self._enforcer = permission_enforcer
        self._known_tools = set(known_tools)
        self._destructive_tools = set(destructive_tools)
        self._network_tools = set(network_tools)

    def refresh_known_tools(self, known_tools: Iterable[str]) -> None:
        self._known_tools = set(known_tools)

    def evaluate(
        self,
        name: str,
        arguments: Dict[str, Any],
        skip_session: bool = False,
    ) -> PermissionDecision:
        if name not in self._known_tools:
            decision = PermissionDecision(
                outcome="deny",
                reason=f"Unknown built-in tool '{name}'",
                source="registry",
            )
            logger.info("permission deny: tool=%s source=%s", name, decision.source)
            return decision

        if self._enforcer is not None:
            tool_full_name = f"builtin__{name}"
            if not self._enforcer.check_tool(tool_full_name):
                decision = PermissionDecision(
                    outcome="deny",
                    reason=(
                        f"Tool '{tool_full_name}' is not permitted "
                        f"by the active profile's permission manifest."
                    ),
                    source="profile",
                )
                logger.info("permission deny: tool=%s source=%s", name, decision.source)
                return decision

        if not skip_session and self._session is not None:
            scope = self._session.needs_permission(name, arguments)
            if scope:
                description = self._session.describe(name, scope, arguments)
                decision = PermissionDecision(
                    outcome="prompt",
                    scope=scope,
                    description=description,
                    source="session",
                )
                logger.info(
                    "permission prompt: tool=%s scope=%s source=%s",
                    name,
                    scope,
                    decision.source,
                )
                return decision

        if self._config.offline_mode and name in self._network_tools:
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

        if self._config.dry_run and name in self._destructive_tools:
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


__all__ = ["PermissionDecision", "PermissionGate", "Outcome"]
