"""Interactive permission broker for the stateful WebSocket endpoint.

The TUI blocks on a modal for tool permissions. REST requests have no
interactive channel, so they keep auto-approving (see ``harness._api_confirm``).
A WebSocket client *does* have a channel: this broker sends a
``permission_request`` frame and waits for the matching ``permission_response``
action, mirroring the TUI's "allow once / allow for session / deny" choices.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Awaitable, Callable, Dict, Optional

logger = logging.getLogger(__name__)

VALID_DECISIONS = frozenset({"once", "session", "deny"})


class PermissionBroker:
    """Track in-flight permission prompts for one WebSocket connection."""

    def __init__(
        self,
        send: Callable[[dict], Awaitable[None]],
        timeout: float = 120.0,
    ):
        self._send = send
        self._timeout = max(0.1, float(timeout))
        self._pending: Dict[str, asyncio.Future] = {}

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    async def request(self, description: str) -> str:
        """Send a prompt and await the client's decision.

        A timeout resolves to ``"deny"`` so an unresponsive or old client can
        never silently keep the legacy auto-approve behaviour.
        """
        request_id = f"perm-{uuid.uuid4().hex}"
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._send(
                {
                    "type": "permission_request",
                    "data": {
                        "id": request_id,
                        "description": description,
                        "options": ["once", "session", "deny"],
                        "default": "deny",
                        "timeout": self._timeout,
                    },
                }
            )
            try:
                return await asyncio.wait_for(future, self._timeout)
            except asyncio.TimeoutError:
                logger.info(
                    "Permission request %s timed out after %.1fs; denying",
                    request_id,
                    self._timeout,
                )
                await self._send(
                    {"type": "permission_timeout", "data": {"id": request_id}}
                )
                return "deny"
        finally:
            self._pending.pop(request_id, None)

    def resolve(self, request_id: str, decision: str) -> bool:
        """Resolve a pending prompt.

        Returns ``False`` for unknown/expired ids. Raises ``ValueError`` for a
        decision outside ``once``/``session``/``deny``.
        """
        if decision not in VALID_DECISIONS:
            raise ValueError(
                f"Invalid permission decision {decision!r}; "
                f"expected one of {sorted(VALID_DECISIONS)}"
            )
        future = self._pending.get(request_id)
        if future is None or future.done():
            return False
        future.set_result(decision)
        return True

    def cancel_all(self, decision: str = "deny") -> int:
        """Resolve every pending prompt (used on interrupt/disconnect)."""
        if decision not in VALID_DECISIONS:
            decision = "deny"
        cancelled = 0
        for future in list(self._pending.values()):
            if not future.done():
                future.set_result(decision)
                cancelled += 1
        return cancelled


__all__ = ["PermissionBroker", "VALID_DECISIONS"]
