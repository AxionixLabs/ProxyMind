# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace
from mcp import types as mcp_types
from mind_app.client_tools.planning import (
    PLAN_STEPS_INPUT_SCHEMA,
    normalize_plan_arguments,
    planning_tools
)
from mind_app.runtime.tools import plan_call as plan_call_module
from mind_app.runtime.tools import plan_steps as plan_steps_module
from mind_app.runtime.tools.plan_call import PlanToolCallRunner
from mind_app.runtime.tools.plan_steps import StepPlanExecutor
from mind_app.runtime.tools.plan_steps_display import render_plan_steps_start
from mind_app.presentation.legacy import LegacyPresentationSink


class FakePlanSession(object):
    """按顺序返回预置 MCP 工具结果。"""

    def __init__(self, results: list[mcp_types.CallToolResult]) -> None:
        self.results = list(results)
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name: str, arguments: dict) -> mcp_types.CallToolResult:
        self.calls.append((name, arguments))
        return self.results.pop(0)


def tool_result(*, ok: bool, text: str) -> mcp_types.CallToolResult:
    """构造计划执行测试使用的 MCP 工具结果。"""
    return mcp_types.CallToolResult(
        content=[mcp_types.TextContent(type="text", text=text)],
        structuredContent={"ok": ok, "text": text},
        isError=not ok
    )


def build_executor(session: FakePlanSession) -> StepPlanExecutor:
    """构造只包含 test_tool 的计划执行器。"""
    return StepPlanExecutor(
        session=session,
        tools=[{"name": "test_tool", "meta": {}}],
        report=SimpleNamespace()
    )


def test_plan_steps_is_classified_as_loop() -> None:
    """计划步骤工具以循环类元数据上报。"""
    tool = next(tool for tool in planning_tools() if tool.name == "plan_steps")

    assert tool.meta["domain"] == "client"
    assert tool.meta["class"] == "loop"


def test_plan_steps_executor_does_not_update_status() -> None:
    """计划执行器直接调用 session，不更新专属状态。"""
    session = FakePlanSession([
        tool_result(ok=True, text="one"),
        tool_result(ok=True, text="two")
    ])

    report = asyncio.run(
        build_executor(session).execute_tool_call(
            arguments={
                "loops": 2,
                "steps": [{"tool": "test_tool", "args": {"value": 1}}]
            }
        )
    )

    assert session.calls == [
        ("test_tool", {"value": 1}),
        ("test_tool", {"value": 1})
    ]
    assert [(item.run, item.index, item.ok, item.text) for item in report.results] == [
        (1, 1, True, "one"),
        (2, 1, True, "two")
    ]
    assert report.ok is True
    assert report.fields["data"]["completed_runs"] == 2
    assert report.fields["data"]["step_results"][0]["last_text"] == "two"


def test_plan_steps_stops_after_failed_step() -> None:
    """步骤失败时按默认策略停止后续计划。"""
    session = FakePlanSession([
        tool_result(ok=False, text="failed"),
        tool_result(ok=True, text="unused")
    ])

    report = asyncio.run(
        build_executor(session).execute_tool_call(
            arguments={
                "steps": [
                    {"tool": "test_tool", "args": {"value": 1}},
                    {"tool": "test_tool", "args": {"value": 2}}
                ]
            }
        )
    )

    assert len(session.calls) == 1
    assert len(report.results) == 1
    assert report.ok is False
    assert report.results[0].text == "failed"
    assert report.fields["data"]["stopped"] is True
    assert report.fields["data"]["failures"][0]["text"] == "failed"


def test_plan_steps_logs_each_step_start_and_result(monkeypatch) -> None:
    """计划步骤记录开始和结果调试日志。"""
    messages: list[str] = []
    monkeypatch.setattr(
        plan_steps_module.logger,
        "debug",
        messages.append
    )
    session = FakePlanSession([tool_result(ok=True, text="done")])

    asyncio.run(
        build_executor(session).execute_tool_call(
            arguments={
                "steps": [{"tool": "test_tool", "args": {}}]
            }
        )
    )

    assert messages[0] == "[PlanSteps] start run=1 step=1 tool=test_tool"
    assert messages[1].startswith(
        "[PlanSteps] result run=1 step=1 tool=test_tool ok=True cost_ms="
    )


def test_plan_steps_schema_does_not_expose_step_meta() -> None:
    """计划步骤签名只声明工具名和参数。"""
    properties = PLAN_STEPS_INPUT_SCHEMA["properties"]["steps"]["items"]["properties"]
    assert set(properties) == {"tool", "args"}


def test_plan_steps_rejects_nested_update_plan() -> None:
    """计划循环不允许嵌套调用展示状态工具。"""
    valid, plan, errors = normalize_plan_arguments({
        "steps": [{"tool": "update_plan", "args": {"plan": []}}]
    })

    assert valid is False
    assert plan["steps"] == []
    assert errors == ["steps[0] nested update_plan forbidden"]


def test_plan_steps_does_not_limit_loops_or_step_count() -> None:
    """计划工具不截断循环次数或步骤数量。"""
    valid, plan, errors = normalize_plan_arguments({
        "loops": 51,
        "steps": [
            {"tool": "test_tool", "args": {}}
            for _ in range(51)
        ]
    })

    schema = PLAN_STEPS_INPUT_SCHEMA["properties"]
    assert valid is True
    assert errors == []
    assert plan["loops"] == 51
    assert len(plan["steps"]) == 51
    assert "maximum" not in schema["loops"]
    assert "maxItems" not in schema["steps"]


def test_plan_steps_start_static_display_omits_step_numbers() -> None:
    """计划步骤静态展示只列工具名，不带步骤序号。"""
    text, _ = render_plan_steps_start({
        "loops": 2,
        "stop_on_fail": False,
        "steps": [
            {"tool": "shell_command", "args": {"command": "echo one"}},
            {"tool": "apply_patch", "args": {"patch": "..."}}
        ]
    })

    assert text == (
        "• Plan Steps\n"
        "  └ loops=2 · steps=2 · stop_on_fail=false\n"
        "    - shell_command\n"
        "    - apply_patch"
    )


def test_plan_steps_start_static_display_truncates_long_plans() -> None:
    """计划步骤静态展示对长计划做有界截断。"""
    text, _ = render_plan_steps_start({
        "steps": [
            {"tool": f"tool_{index}", "args": {}}
            for index in range(10)
        ]
    })

    assert "    - tool_0" in text
    assert "    - tool_7" in text
    assert "    - tool_8" not in text
    assert "    ... 2 more" in text


def test_plan_tool_handler_uses_static_display_tool_status_and_posts_result(monkeypatch) -> None:
    """计划工具显示静态块，执行期间使用通用工具状态，并在执行后回传报告。"""
    events: list[tuple[str, object]] = []
    report = SimpleNamespace(ok=True, fields={"ok": True}, text="done")

    class FakeExecutor(object):
        async def execute_tool_call(self, *, arguments: dict, **runtime: object) -> object:
            events.append(("execute", arguments))
            events.append(("runtime", runtime))
            return report

    class FakeStreamUI(object):
        BLOCK = "block"

        def record_tool_arguments(self, *args, **kwargs) -> None:
            events.append(("audit", (args, kwargs)))

        async def feed(self, *args, **kwargs) -> None:
            events.append(("feed", (args, kwargs)))

        async def begin_tool_status(self) -> None:
            events.append(("begin_tool", None))

        async def end_status(self, *, immediate: bool = False) -> None:
            events.append(("end_status", immediate))

    async def fake_show_result(*args, **kwargs) -> None:
        events.append(("result", (args, kwargs)))

    async def fake_post_result(*args, **kwargs) -> dict:
        events.append(("post", (args, kwargs)))
        return {}

    monkeypatch.setattr(plan_call_module, "show_tool_result", fake_show_result)
    monkeypatch.setattr(plan_call_module.request, "post_tool_result", fake_post_result)

    stream_ui = FakeStreamUI()
    runner = PlanToolCallRunner(
        session=SimpleNamespace(),
        stream_ui=stream_ui,
        presentation=LegacyPresentationSink(stream_ui),
        tools=[],
        report=SimpleNamespace()
    )
    runner.executor = FakeExecutor()

    asyncio.run(
        runner.handle(
            event={"cid": "cid", "sid": "sid", "call_id": "call"},
            arguments={"steps": []}
        )
    )

    assert [name for name, _ in events] == [
        "audit", "feed", "begin_tool", "execute", "runtime", "end_status", "result", "post"
    ]
    assert events[4][1] == {"cid": "cid", "sid": "sid", "call_id": "call"}
    post_args, _ = events[-1][1]
    assert post_args[3:6] == ("plan_steps", True, {"ok": True})
