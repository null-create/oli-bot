"""Full agent tool-loop over real wire with real tool execution.

The model output is scripted over real HTTP/SSE, the agent parses tool calls
and executes them through the real ``BuiltinToolManager`` — including the real
``create_subprocess_shell`` for ``run_command`` and real file I/O. Permission
gating is exercised via an auto-approve ``confirm_callback``.
"""

from __future__ import annotations

import pytest

from .conftest import cc, tool_delta


@pytest.mark.integration
async def test_run_command_executes_real_subprocess(make_full_agent, mock_openai, auto_allow, tmp_path):
    agent, _ = make_full_agent(workspace=None, offline_mode=False)
    mock_openai.script(
        (
            "stream",
            [
                cc(
                    delta={
                        "tool_calls": [
                            tool_delta(
                                0,
                                tc_id="t1",
                                name="builtin__run_command",
                                args=f'{{"command": "echo hello from integration", "workdir": "{tmp_path}"}}',
                            )
                        ]
                    }
                ),
                cc(finish="tool_calls", usage={"prompt_tokens": 4, "completion_tokens": 2}),
            ],
        ),
        (
            "stream",
            [
                cc(content="Command output captured."),
                cc(finish="stop", usage={"prompt_tokens": 6, "completion_tokens": 3}),
            ],
        ),
    )

    from oli_bot.models import Done, Message, ToolCallResult

    messages = [Message(role="user", content="run echo hello from integration")]
    events = [ev async for ev in agent.process(messages, confirm_callback=auto_allow)]
    results = [e for e in events if isinstance(e, ToolCallResult)]
    assert len(results) == 1
    assert "Exit code: 0" in results[0].result
    assert "hello from integration" in results[0].result
    finals = [e.full_text for e in events if isinstance(e, Done)]
    assert finals == ["Command output captured."]
    assert any(msg.role == "tool" and "hello from integration" in msg.content for msg in messages)


@pytest.mark.integration
async def test_git_readonly_allowed_push_denied(
    make_full_agent, mock_openai, auto_allow, tmp_path
):
    import subprocess

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    agent, _ = make_full_agent(workspace=tmp_path, offline_mode=False)
    mock_openai.script(
        (
            "stream",
            [
                cc(
                    delta={
                        "tool_calls": [
                            tool_delta(
                                0,
                                tc_id="s1",
                                name="builtin__run_command",
                                args=f'{{"command": "git status --short", "workdir": "{tmp_path}"}}',
                            )
                        ]
                    }
                ),
                cc(finish="tool_calls", usage={"prompt_tokens": 3, "completion_tokens": 2}),
            ],
        ),
        (
            "stream",
            [
                cc(
                    delta={
                        "tool_calls": [
                            tool_delta(
                                1,
                                tc_id="s2",
                                name="builtin__run_command",
                                args=f'{{"command": "git push", "workdir": "{tmp_path}"}}',
                            )
                        ]
                    }
                ),
                cc(finish="tool_calls", usage={"prompt_tokens": 3, "completion_tokens": 2}),
            ],
        ),
        (
            "stream",
            [
                cc(content="done"),
                cc(finish="stop", usage={"prompt_tokens": 5, "completion_tokens": 2}),
            ],
        ),
    )

    from oli_bot.models import Done, Message, ToolCallResult

    messages = [Message(role="user", content="check git state then push")]
    events = [ev async for ev in agent.process(messages, confirm_callback=auto_allow)]
    results = [e for e in events if isinstance(e, ToolCallResult)]
    assert len(results) == 2
    assert "Exit code: 0" in results[0].result
    denied = results[1].result.lower()
    assert "error" in denied and "push" in denied
    assert any(e.full_text == "done" for e in events if isinstance(e, Done))


@pytest.mark.integration
async def test_write_then_read_file_via_tools(
    make_full_agent, mock_openai, auto_allow, tmp_path
):
    target = tmp_path / "note.txt"
    agent, _ = make_full_agent(workspace=tmp_path, offline_mode=False)
    mock_openai.script(
        (
            "stream",
            [
                cc(
                    delta={
                        "tool_calls": [
                            tool_delta(
                                0,
                                tc_id="w1",
                                name="builtin__write_file",
                                args=f'{{"file_path": "{target}", "content": "hello world"}}',
                            )
                        ]
                    }
                ),
                cc(finish="tool_calls", usage={"prompt_tokens": 4, "completion_tokens": 2}),
            ],
        ),
        (
            "stream",
            [
                cc(
                    delta={
                        "tool_calls": [
                            tool_delta(
                                1,
                                tc_id="r1",
                                name="builtin__read_file",
                                args=f'{{"file_path": "{target}"}}',
                            )
                        ]
                    }
                ),
                cc(finish="tool_calls", usage={"prompt_tokens": 4, "completion_tokens": 2}),
            ],
        ),
        (
            "stream",
            [
                cc(content="Wrote and read back."),
                cc(finish="stop", usage={"prompt_tokens": 5, "completion_tokens": 2}),
            ],
        ),
    )

    from oli_bot.models import Done, Message, ToolCallResult

    messages = [Message(role="user", content="write a note then read it")]
    events = [ev async for ev in agent.process(messages, confirm_callback=auto_allow)]
    results = [e.result for e in events if isinstance(e, ToolCallResult)]
    assert len(results) == 2
    assert target.exists()
    assert "hello world" in results[1]
    assert any(e.full_text == "Wrote and read back." for e in events if isinstance(e, Done))