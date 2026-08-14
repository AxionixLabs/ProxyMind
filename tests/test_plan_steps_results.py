# -*- coding: utf-8 -*-

from types import SimpleNamespace
from pathlib import Path

import pytest
from mcp import types as mcp_types

from mind_app.client_tools.planning import (
    PLAN_STEPS_INPUT_SCHEMA,
    planning_tools,
)
from mind_app.runtime.tools.plan_steps import StepPlanExecutor
from mind_app.runtime.execution import AgentContext, TurnContext
from mind_app.runtime.hooks.runtime import HookRuntime
from mind_app.runtime.hooks.scope import (
    HookExecutionContext,
    HookExecutionScope
)
from mind_app.runtime.hooks.tool import ToolCallCoordinator
from mind_core.hook_discovery import resolve_hook_definitions
from mind_core.permissions import preset_permissions


class _PlanSession(object):
    """按顺序返回预置的步骤结果。"""

    def __init__(self, results: list[mcp_types.CallToolResult]) -> None:
        self.results = list(results)
        self.calls = []

    async def call_tool(
        self,
        name: str,
        arguments: dict,
        **kwargs,
    ) -> mcp_types.CallToolResult:
        self.calls.append((name, arguments, kwargs))
        return self.results.pop(0)


class _HookRunner(object):
    """按命令返回预置 Hook 输出。"""

    def __init__(self, outputs: dict[str, dict]) -> None:
        self.outputs = dict(outputs)
        self.calls = []

    async def execute(self, definition, payload):
        self.calls.append((definition, payload))
        return SimpleNamespace(data=dict(
            self.outputs.get(definition.handler.command) or {}
        ))


def _tool_result(
    text: str,
    *,
    ok: bool = True,
    data: dict | None = None,
    attachments: list[dict] | None = None,
) -> mcp_types.CallToolResult:
    """构造包含标准结果字段的工具响应。"""
    return mcp_types.CallToolResult(
        content=[mcp_types.TextContent(type="text", text=text)],
        structuredContent={
            "ok": ok,
            "text": text,
            "attachments": list(attachments or []),
            "data": dict(data or {}),
        },
        isError=not ok,
    )


def _executor(
    results: list[mcp_types.CallToolResult],
    *,
    hooks: dict | None = None,
    hook_outputs: dict[str, dict] | None = None,
) -> StepPlanExecutor:
    """构造只包含一个测试工具的步骤执行器。"""
    turn_context = TurnContext.create(
        agent=AgentContext.root("sid"),
        cid="cid",
        sid="sid",
        source="test",
        pref_config={},
        cwd=".",
        permissions=preset_permissions("auto"),
        turn_id="turn_plan",
    )
    definitions = resolve_hook_definitions(
        hooks or {},
        source_scope="user",
        source_path=Path("config.toml"),
    )
    runtime = HookRuntime(
        definitions,
        command_runner=_HookRunner(hook_outputs or {}),
    )
    return StepPlanExecutor(
        session=_PlanSession(results),
        tools=[{"name": "test_tool"}],
        turn_context=turn_context,
        pref_config={"primary": {"model": "test-model"}},
        tool_call_coordinator=ToolCallCoordinator(HookExecutionScope(
            context=HookExecutionContext.from_turn(turn_context),
            dispatcher=runtime,
        )),
    )


@pytest.mark.anyio
async def test_single_run_returns_complete_step_results() -> None:
    text = f"first line\n{'x' * 1200}\nlast line"
    attachment = {
        "kind": "resource_link",
        "name": "report",
        "uri": "file:///report.json",
        "mime_type": "application/json",
    }
    structured_data = {
        "items": [{"id": 1}, {"id": 2}],
        "next": "cursor-2",
    }

    report = await _executor([
        _tool_result(
            text,
            data=structured_data,
            attachments=[attachment],
        )
    ]).execute_tool_call(arguments={
        "loops": 1,
        "steps": [{"tool": "test_tool", "args": {}}],
    })

    returned = report.fields["data"]["results"][0]

    assert report.text != text
    assert report.fields["attachments"] == [attachment]
    assert report.fields["data"]["result_mode"] == "full"
    assert report.fields["data"]["steps"][0]["ok_count"] == 1
    assert returned["result"]["text"] == text
    assert returned["result"]["data"] == structured_data
    assert returned["result"]["attachments"] == [attachment]


@pytest.mark.anyio
async def test_plan_step_preserves_client_tool_runtime_config() -> None:
    executor = _executor([_tool_result("done")])

    await executor.execute_tool_call(arguments={
        "steps": [{"tool": "test_tool", "args": {}}],
    })

    assert executor.session.calls[0][2]["pref_config"] == {
        "primary": {"model": "test-model"},
    }


@pytest.mark.anyio
async def test_plan_step_executes_pre_hook_updated_input() -> None:
    executor = _executor(
        [_tool_result("done")],
        hooks={
            "PreToolUse": [{
                "hooks": [{"type": "command", "command": "rewrite"}],
                "matcher": "test_tool",
            }],
        },
        hook_outputs={
            "rewrite": {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "updatedInput": {"value": 2},
                },
            },
        },
    )

    await executor.execute_tool_call(arguments={
        "steps": [{"tool": "test_tool", "args": {"value": 1}}],
    })

    assert executor.session.calls[0][1] == {"value": 2}


@pytest.mark.anyio
async def test_plan_step_preserves_pre_hook_denial_context() -> None:
    report = await _executor(
        [],
        hooks={
            "PreToolUse": [{
                "hooks": [{"type": "command", "command": "deny"}],
                "matcher": "test_tool",
            }],
        },
        hook_outputs={
            "deny": {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "unsafe operation",
                    "additionalContext": "Use the safe tool instead.",
                },
            },
        },
    ).execute_tool_call(arguments={
        "steps": [{"tool": "test_tool", "args": {}}],
    })

    assert report.additional_context == ("Use the safe tool instead.",)


@pytest.mark.anyio
async def test_plan_step_applies_post_hook_result_and_feedback() -> None:
    executor = _executor(
        [_tool_result("secret", data={"secret": True})],
        hooks={
            "PostToolUse": [{
                "hooks": [{"type": "command", "command": "redact"}],
                "matcher": "test_tool",
            }],
        },
        hook_outputs={
            "redact": {
                "replacementResult": {
                    "ok": False,
                    "text": "redacted",
                    "data": {"redacted": True},
                },
                "systemMessage": "Do not expose the original result.",
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": "explain the redaction",
                },
            },
        },
    )

    report = await executor.execute_tool_call(arguments={
        "stop_on_fail": False,
        "steps": [{"tool": "test_tool", "args": {}}],
    })

    step = report.results[0]
    assert step.ok is False
    assert step.text == "redacted"
    assert step.result["data"] == {"redacted": True}
    assert report.additional_context == ("explain the redaction",)


@pytest.mark.anyio
async def test_repeated_runs_return_untruncated_aggregate() -> None:
    first_text = f"run one\n{'a' * 1000}"
    last_text = f"run two failed\n{'b' * 1000}"

    report = await _executor([
        _tool_result(first_text),
        _tool_result(last_text, ok=False),
    ]).execute_tool_call(arguments={
        "loops": 2,
        "stop_on_fail": False,
        "steps": [{"tool": "test_tool", "args": {}}],
    })

    data = report.fields["data"]

    assert "results" not in data
    assert report.fields["attachments"] == []
    assert data["result_mode"] == "aggregate"
    assert data["steps"][0]["call_count"] == 2
    assert data["steps"][0]["ok_count"] == 1
    assert data["steps"][0]["fail_count"] == 1
    assert first_text not in str(data)
    assert data["failure_groups"] == [{
        "step": 1,
        "tool": "test_tool",
        "reason": last_text,
        "count": 1,
        "first_run": 2,
        "last_run": 2,
    }]
    assert data["omitted_failure_groups"] == 0


@pytest.mark.anyio
async def test_repeated_failures_are_grouped_by_reason() -> None:
    repeated = "connection refused"
    distinct = "request timed out"

    report = await _executor([
        _tool_result(repeated, ok=False),
        _tool_result(repeated, ok=False),
        _tool_result(distinct, ok=False),
        _tool_result(repeated, ok=False),
    ]).execute_tool_call(arguments={
        "loops": 4,
        "stop_on_fail": False,
        "steps": [{"tool": "test_tool", "args": {}}],
    })

    assert report.fields["data"]["failure_groups"] == [
        {
            "step": 1,
            "tool": "test_tool",
            "reason": repeated,
            "count": 3,
            "first_run": 1,
            "last_run": 4,
        },
        {
            "step": 1,
            "tool": "test_tool",
            "reason": distinct,
            "count": 1,
            "first_run": 3,
            "last_run": 3,
        },
    ]


def test_plan_description_explains_result_policy() -> None:
    description = planning_tools()[0].description
    loops_description = PLAN_STEPS_INPUT_SCHEMA["properties"]["loops"][
        "description"
    ]

    for value in (description, loops_description):
        assert "loops=1" in value
        assert "完整结果" in value
        assert "loops>1" in value
        assert "数字统计" in value
        assert "不回传成功步骤的具体输出" in value
