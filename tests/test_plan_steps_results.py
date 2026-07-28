# -*- coding: utf-8 -*-

from types import SimpleNamespace

import pytest
from mcp import types as mcp_types

from mind_app.client_tools.planning import (
    PLAN_STEPS_INPUT_SCHEMA,
    planning_tools,
)
from mind_app.runtime.tools.plan_steps import StepPlanExecutor


class _PlanSession(object):
    """按顺序返回预置的步骤结果。"""

    def __init__(self, results: list[mcp_types.CallToolResult]) -> None:
        self.results = list(results)

    async def call_tool(
        self,
        name: str,
        arguments: dict,
        **kwargs,
    ) -> mcp_types.CallToolResult:
        _ = name, arguments, kwargs
        return self.results.pop(0)


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


def _executor(results: list[mcp_types.CallToolResult]) -> StepPlanExecutor:
    """构造只包含一个测试工具的步骤执行器。"""
    return StepPlanExecutor(
        session=_PlanSession(results),
        tools=[{"name": "test_tool"}],
        report=SimpleNamespace(),
        turn_context=SimpleNamespace(
            cid="cid",
            sid="sid",
            permissions=None,
        ),
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
