# -*- coding: utf-8 -*-

import pytest
from mcp import types as mcp_types
from agent.application.turns.context import (
    AgentContext,
    ToolInvocation,
    TurnContext,
)
from agent.domain.policies import preset_permissions
from infrastructure.mcp.tool_invocation import execute_tool


class _ProgressSession(object):
    """记录工具路由传入的进度回调。"""

    def __init__(self, *, message: str | None = None) -> None:
        self.message = message
        self.received_callback = False

    async def call_tool(self, _name, _arguments, **kwargs):
        callback = kwargs.get("progress_callback")
        self.received_callback = callback is not None
        if callback is not None:
            await callback(1.0, 2.0, self.message)
        return mcp_types.CallToolResult(content=[])


def _invocation(name: str) -> ToolInvocation:
    """构造工具路由测试使用的完整调用上下文。"""
    turn = TurnContext.create(
        agent=AgentContext.root("sid_progress"),
        cid="cid_progress",
        sid="sid_progress",
        source="test",
        pref_config={},
        cwd=".",
        permissions=preset_permissions("auto"),
        turn_id="turn_progress",
    )
    return ToolInvocation(
        turn=turn,
        name=name,
        arguments={},
        call_id="call_progress",
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("message", "expected"),
    (
        ("halfway", "halfway"),
        (None, "coding_edit progress=1.0/2.0"),
    ),
)
async def test_coding_tool_progress_is_forwarded_to_stream(
    message: str | None,
    expected: str,
) -> None:
    session = _ProgressSession(message=message)
    streamed: list[str] = []

    async def stream(text: str) -> None:
        streamed.append(text)

    await execute_tool(
        session,
        tools=[{"name": "coding_edit"}],
        invocation=_invocation("coding_edit"),
        pref_config={},
        stream_callback=stream,
        enable_progress_notify=True,
    )

    assert session.received_callback is True
    assert streamed == [expected]


@pytest.mark.anyio
async def test_non_coding_tool_does_not_register_progress_callback() -> None:
    session = _ProgressSession(message="ignored")

    await execute_tool(
        session,
        tools=[{"name": "inspect_page"}],
        invocation=_invocation("inspect_page"),
        pref_config={},
        enable_progress_notify=True,
    )

    assert session.received_callback is False
