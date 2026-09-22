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
async def test_run_command_executes_real_subprocess(
    make_full_agent, mock_openai, auto_allow, tmp_path
):
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
                cc(
                    finish="tool_calls",
                    usage={"prompt_tokens": 4, "completion_tokens": 2},
                ),
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
    assert any(
        msg.role == "tool" and "hello from integration" in msg.content
        for msg in messages
    )


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
                cc(
                    finish="tool_calls",
                    usage={"prompt_tokens": 3, "completion_tokens": 2},
                ),
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
                cc(
                    finish="tool_calls",
                    usage={"prompt_tokens": 3, "completion_tokens": 2},
                ),
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
                cc(
                    finish="tool_calls",
                    usage={"prompt_tokens": 4, "completion_tokens": 2},
                ),
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
                cc(
                    finish="tool_calls",
                    usage={"prompt_tokens": 4, "completion_tokens": 2},
                ),
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
    assert any(
        e.full_text == "Wrote and read back." for e in events if isinstance(e, Done)
    )


@pytest.mark.integration
async def test_question_tool_awaits_user_answer_and_resumes(
    make_full_agent, mock_openai, auto_allow
):
    agent, session = make_full_agent(workspace=None, offline_mode=False)
    mocked_answers = "Question 1: Which environment?\nUser answer: Production"

    def fake_question_callback(questions):
        assert questions == [
            {
                "question": "Which environment?",
                "options": ["Staging", "Production"],
                "recommended": "2",
            }
        ]
        return mocked_answers

    agent.mcp_manager._builtin_tools.set_question_callback(fake_question_callback)

    mock_openai.script(
        (
            "stream",
            [
                cc(
                    delta={
                        "tool_calls": [
                            tool_delta(
                                0,
                                tc_id="q1",
                                name="builtin__question",
                                args=(
                                    '{"questions": [{"question": "Which environment?", '
                                    '"options": ["Staging", "Production"], '
                                    '"recommended": "2"}]}'
                                ),
                            )
                        ]
                    }
                ),
                cc(
                    finish="tool_calls",
                    usage={"prompt_tokens": 4, "completion_tokens": 2},
                ),
            ],
        ),
        (
            "stream",
            [
                cc(content="Deploying to production as you chose."),
                cc(finish="stop", usage={"prompt_tokens": 6, "completion_tokens": 3}),
            ],
        ),
    )

    from oli_bot.models import Done, Message, ToolCallResult

    messages = [Message(role="user", content="pick the deploy target")]
    events = [ev async for ev in agent.process(messages, confirm_callback=auto_allow)]
    results = [e for e in events if isinstance(e, ToolCallResult)]
    assert len(results) == 1
    assert results[0].name == "builtin__question"
    assert results[0].result == mocked_answers
    assert any(
        e.full_text == "Deploying to production as you chose."
        for e in events
        if isinstance(e, Done)
    )
    assert any(
        msg.role == "tool" and "User answer: Production" in msg.content
        for msg in messages
    )
    # The question tool never falls through to a permission prompt, so the
    # session scope list must remain empty.
    assert not session._session_grants


@pytest.mark.integration
async def test_question_tool_without_callback_returns_error(
    make_full_agent, mock_openai, auto_allow
):
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
                                tc_id="q1",
                                name="builtin__question",
                                args='{"questions": [{"question": "Proceed?"}]}',
                            )
                        ]
                    }
                ),
                cc(
                    finish="tool_calls",
                    usage={"prompt_tokens": 4, "completion_tokens": 2},
                ),
            ],
        ),
        (
            "stream",
            [
                cc(content="Proceeding on my own."),
                cc(finish="stop", usage={"prompt_tokens": 6, "completion_tokens": 3}),
            ],
        ),
    )

    from oli_bot.models import Done, Message, ToolCallResult

    messages = [Message(role="user", content="ask me something")]
    events = [ev async for ev in agent.process(messages, confirm_callback=auto_allow)]
    results = [e for e in events if isinstance(e, ToolCallResult)]
    assert len(results) == 1
    assert results[0].result.startswith("Error:")
    assert "interactive user" in results[0].result
    assert any(
        e.full_text == "Proceeding on my own." for e in events if isinstance(e, Done)
    )
