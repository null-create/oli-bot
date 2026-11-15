"""Tests for the WebSocket permission broker."""

import asyncio

import pytest

from oli_bot.api.permissions import PermissionBroker


async def _wait_for(predicate, attempts=200):
    for _ in range(attempts):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition never became true")


async def test_broker_sends_request_and_resolves():
    sent = []

    async def send(frame):
        sent.append(frame)

    broker = PermissionBroker(send, timeout=5.0)
    pending = asyncio.create_task(broker.request("Write to /tmp/x"))
    await _wait_for(lambda: bool(sent))
    frame = sent[0]
    assert frame["type"] == "permission_request"
    data = frame["data"]
    assert data["description"] == "Write to /tmp/x"
    assert data["options"] == ["once", "session", "deny"]
    assert data["default"] == "deny"
    assert broker.pending_count == 1

    assert broker.resolve(data["id"], "session") is True
    assert await pending == "session"
    assert broker.pending_count == 0


async def test_broker_timeout_denies_and_notifies():
    sent = []

    async def send(frame):
        sent.append(frame)

    broker = PermissionBroker(send, timeout=0.01)
    assert await broker.request("Read secret") == "deny"
    assert [f["type"] for f in sent] == [
        "permission_request",
        "permission_timeout",
    ]


async def test_broker_rejects_invalid_decision_and_unknown_id():
    async def send(frame):
        return None

    broker = PermissionBroker(send, timeout=1.0)
    with pytest.raises(ValueError):
        broker.resolve("missing", "allow")
    assert broker.resolve("missing", "deny") is False


async def test_broker_cancel_all_resolves_pending_prompts():
    sent = []

    async def send(frame):
        sent.append(frame)

    broker = PermissionBroker(send, timeout=5.0)
    pending = asyncio.create_task(broker.request("Do it"))
    await _wait_for(lambda: bool(sent))
    assert broker.cancel_all("deny") == 1
    assert await pending == "deny"
    assert broker.pending_count == 0
