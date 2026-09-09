# -*- coding: utf-8 -*-

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import (
    AsyncMock,
    Mock,
)

import pytest
from mcp import types as mcp_types

from agent.application.approvals.models import ApprovalOutcome
from agent.application.hooks.models import (
    HookVisibleToolResult,
    ToolCallRunResult,
)
from agent.application.turns.context import (
    AgentContext,
    ToolInvocation,
    TurnContext,
)
from agent.domain.approvals import McpApprovalAction
from agent.domain.policies import (
    PermissionSettings,
    preset_permissions,
)
from agent.harness.tools.client_calls import ClientToolCallRunner
from agent.ports.persistence import EffectJournalDecision
from infrastructure.mcp.composite_session import CompositeToolSession
from infrastructure.mcp.tool_execution import McpToolExecutionAdapter
from protocol.schema.stream_events import ExecutionEffect


class _Approval:
    """记录类型化 MCP 动作并返回指定用户决定。"""

    def __init__(self, decision: str) -> None:
        self.decision = decision
        self.actions: list[McpApprovalAction] = []
        self.presentations: list[dict[str, object]] = []

    async def request_outcome(self, approval):
        raise AssertionError("MCP gate must not rebuild its action from a mapping")

    async def request_action_outcome(self, action, presentation):
        self.actions.append(action)
        self.presentations.append(dict(presentation))
        return ApprovalOutcome.create(
            self.decision,
            source="user",
            reason="user",
        )


async def _allow_hook(invocation, operation):
    operation_result = await operation(invocation)
    return ToolCallRunResult(
        allowed=True,
        value=operation_result.value,
        visible_result=HookVisibleToolResult(
            ok=operation_result.snapshot.ok,
            text=operation_result.snapshot.text,
            fields=operation_result.snapshot.fields,
        ),
    )


def _tool(
    mode: str,
    *,
    read_only: bool | None = None,
) -> mcp_types.Tool:
    return mcp_types.Tool(
        name="lookup",
        title="Lookup docs",
        description="Read a documentation record.",
        inputSchema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
        annotations=mcp_types.ToolAnnotations(readOnlyHint=read_only),
        _meta={
            "server": "docs",
            "transport": "stdio",
            "approval_mode": mode,
            "approval_allow_session": True,
            "approval_allow_persistent": True,
        },
    )


def _runtime(
    mode: str,
    *,
    decision: str = "accept",
    read_only: bool | None = None,
    full_access: bool = False,
):
    name = "mcp__docs__lookup"
    approval = _Approval(decision)
    external = SimpleNamespace(
        tools={name: _tool(mode, read_only=read_only)},
        call_tool=AsyncMock(return_value=mcp_types.CallToolResult(
            content=[mcp_types.TextContent(type="text", text="done")],
        )),
    )
    session = CompositeToolSession(external_group=external)
    turn = TurnContext.create(
        agent=AgentContext.root("sid-mcp"),
        cid="cid-mcp",
        sid="sid-mcp",
        source="test",
        pref_config={},
        cwd="D:/workspace",
        permissions=preset_permissions("full-access" if full_access else "auto"),
        approval_coordinator=approval,
        turn_id="turn-mcp",
    )
    invocation = ToolInvocation(
        turn=turn,
        call_id="call-mcp",
        name=name,
        arguments={"query": "approval"},
        meta={"external": True, "server": "docs", "transport": "stdio"},
    )
    presentation = SimpleNamespace(emit=AsyncMock())
    runner = ClientToolCallRunner(
        session=session,
        output_control=SimpleNamespace(record_tool_arguments=Mock()),
        presentation=presentation,
        tools=[{
            "name": name,
            "description": "external",
            "inputSchema": external.tools[name].inputSchema,
            "meta": dict(invocation.meta or {}),
        }],
        pref_config={},
        tool_call_coordinator=SimpleNamespace(
            run_invocation=AsyncMock(side_effect=_allow_hook),
        ),
        tool_execution=McpToolExecutionAdapter(),
        activity=SimpleNamespace(
            tool_started=AsyncMock(),
            tool_completed=AsyncMock(),
            approval_started=AsyncMock(),
            approval_completed=AsyncMock(),
        ),
        effect_journal=SimpleNamespace(),
    )
    return runner, invocation, approval, external, presentation


@pytest.mark.anyio
async def test_prompt_mode_approves_before_external_mcp_execution() -> None:
    runner, invocation, approval, external, presentation = _runtime("prompt")

    outcome = await runner.execute(invocation, use_coding_trace=False, display=False)

    assert outcome.result.ok is True
    assert len(approval.actions) == 1
    assert approval.actions[0].descriptor.tool_name == "lookup"
    assert approval.presentations[0]["arguments"] == {"query": "approval"}
    external.call_tool.assert_awaited_once()
    presentation.emit.assert_awaited_once()


@pytest.mark.anyio
async def test_subagent_mcp_approval_projects_trusted_source_identity() -> None:
    runner, invocation, approval, external, _presentation = _runtime("prompt")
    agent = AgentContext.root("sid-mcp").child(
        "worker",
        "research",
        agent_id="agent-worker",
    )
    child_invocation = replace(
        invocation,
        turn=replace(invocation.turn, agent=agent),
    )

    outcome = await runner.execute(
        child_invocation,
        use_coding_trace=False,
        display=False,
    )

    assert outcome.result.ok is True
    assert approval.presentations[0]["agent_id"] == "agent-worker"
    assert approval.presentations[0]["agent_type"] == "worker"
    assert approval.presentations[0]["agent_depth"] == 1
    external.call_tool.assert_awaited_once()


@pytest.mark.anyio
async def test_declined_external_mcp_call_never_reaches_sdk() -> None:
    runner, invocation, approval, external, _presentation = _runtime(
        "prompt",
        decision="decline",
    )

    outcome = await runner.execute(invocation, use_coding_trace=False, display=False)

    assert outcome.result.ok is False
    assert outcome.result.fields["data"]["approval_denied"] is True
    assert len(approval.actions) == 1
    external.call_tool.assert_not_awaited()


@pytest.mark.anyio
async def test_full_access_auto_approves_mcp_when_policy_is_never() -> None:
    runner, invocation, approval, external, _presentation = _runtime(
        "prompt",
        full_access=True,
    )

    outcome = await runner.execute(invocation, use_coding_trace=False, display=False)

    assert outcome.result.ok is True
    assert approval.actions == []
    external.call_tool.assert_awaited_once()


@pytest.mark.anyio
async def test_never_policy_still_fails_closed_in_restricted_sandbox() -> None:
    runner, invocation, approval, external, _presentation = _runtime("prompt")
    restricted_turn = replace(
        invocation.turn,
        permissions=PermissionSettings(
            sandbox_mode="read-only",
            approval_policy="never",
        ),
    )
    restricted_invocation = replace(invocation, turn=restricted_turn)

    outcome = await runner.execute(
        restricted_invocation,
        use_coding_trace=False,
        display=False,
    )

    assert outcome.result.ok is False
    assert "policy is never" in outcome.result.text
    assert approval.actions == []
    external.call_tool.assert_not_awaited()


@pytest.mark.anyio
async def test_writes_mode_allows_explicit_read_only_tool_without_card() -> None:
    runner, invocation, approval, external, presentation = _runtime(
        "writes",
        read_only=True,
    )

    outcome = await runner.execute(invocation, use_coding_trace=False, display=False)

    assert outcome.result.ok is True
    assert approval.actions == []
    external.call_tool.assert_awaited_once()
    presentation.emit.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("mode", "read_only"),
    (("approve", None), ("writes", True)),
)
async def test_policy_approved_mcp_call_still_requires_stable_identity(
    mode: str,
    read_only: bool | None,
) -> None:
    runner, invocation, approval, external, presentation = _runtime(
        mode,
        read_only=read_only,
    )
    missing_identity = replace(invocation, call_id="")

    outcome = await runner.execute(
        missing_identity,
        use_coding_trace=False,
        display=False,
    )

    assert outcome.result.ok is False
    assert "tool_call_id is required" in outcome.result.text
    assert approval.actions == []
    external.call_tool.assert_not_awaited()
    presentation.emit.assert_not_awaited()


@pytest.mark.anyio
async def test_external_mcp_session_requires_call_and_turn_identity() -> None:
    runner, invocation, _approval, external, _presentation = _runtime("approve")
    session = runner.session

    with pytest.raises(ValueError, match="call_id is required"):
        await session.call_tool(
            invocation.name,
            invocation.arguments,
            turn_context=invocation.turn,
        )
    with pytest.raises(TypeError, match="turn context is required"):
        await session.call_tool(
            invocation.name,
            invocation.arguments,
            call_id=invocation.call_id,
        )

    external.call_tool.assert_not_awaited()


@pytest.mark.anyio
async def test_invalid_mcp_arguments_fail_before_approval_and_execution() -> None:
    runner, invocation, approval, external, _presentation = _runtime("prompt")
    invalid = ToolInvocation(
        turn=invocation.turn,
        call_id=invocation.call_id,
        name=invocation.name,
        arguments={"query": 7},
        meta=invocation.meta,
    )

    outcome = await runner.execute(invalid, use_coding_trace=False, display=False)

    assert outcome.result.ok is False
    assert "arguments are invalid" in outcome.result.text
    assert approval.actions == []
    external.call_tool.assert_not_awaited()


@pytest.mark.anyio
async def test_mcp_server_identity_conflict_fails_closed() -> None:
    runner, invocation, approval, external, _presentation = _runtime("prompt")
    conflict = ToolInvocation(
        turn=invocation.turn,
        call_id=invocation.call_id,
        name=invocation.name,
        arguments=invocation.arguments,
        meta={**(invocation.meta or {}), "server": "other"},
    )

    outcome = await runner.execute(conflict, use_coding_trace=False, display=False)

    assert outcome.result.ok is False
    assert "identity conflicts" in outcome.result.text
    assert approval.actions == []
    external.call_tool.assert_not_awaited()


@pytest.mark.anyio
async def test_mcp_approval_precedes_formal_effect_and_commits_result() -> None:
    runner, invocation, approval, external, _presentation = _runtime("prompt")
    journal = SimpleNamespace(
        inspect=AsyncMock(return_value=EffectJournalDecision("execute")),
        begin=AsyncMock(return_value=EffectJournalDecision("execute")),
        commit=AsyncMock(),
        mark_unknown=AsyncMock(),
    )
    runner.effect_journal = journal
    durable = replace(
        invocation,
        effect=ExecutionEffect(
            effect_id="effect-mcp-call",
            fingerprint="a" * 64,
            replay="manual",
        ),
    )

    outcome = await runner.execute(durable, use_coding_trace=False, display=False)

    assert outcome.result.ok is True
    assert len(approval.actions) == 1
    journal.inspect.assert_awaited_once()
    journal.begin.assert_awaited_once()
    journal.commit.assert_awaited_once()
    external.call_tool.assert_awaited_once()
