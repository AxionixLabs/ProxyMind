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


class FakePlanUI(object):
    """记录计划执行期间的状态和审计调用。"""

    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    async def begin_loop_status(self, summary: str | None = None) -> None:
        self.events.append(("begin", summary))

    async def update_loop_status_summary(self, summary: str) -> None:
        self.events.append(("update", summary))

    async def end_status(self, *, immediate: bool = False) -> None:
        self.events.append(("end", immediate))

    def record_tool_arguments(self, name: str, arguments: dict) -> None:
        self.events.append(("audit", (name, arguments)))


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


def build_executor(session: FakePlanSession, stream_ui: FakePlanUI) -> StepPlanExecutor:
    """构造只包含 test_tool 的计划执行器。"""
    return StepPlanExecutor(
        session=session,
        stream_ui=stream_ui,
        tools=[{"name": "test_tool", "meta": {}}],
        report=SimpleNamespace()
    )


def test_plan_steps_is_classified_as_loop() -> None:
    """计划步骤工具以循环类元数据上报。"""
    tool = next(tool for tool in planning_tools() if tool.name == "plan_steps")

    assert tool.meta["domain"] == "client"
    assert tool.meta["class"] == "loop"


def test_plan_steps_runs_in_internal_loop_and_cleans_status() -> None:
    """计划工具直接循环调用 session，并独占 loop 状态。"""
    session = FakePlanSession([
        tool_result(ok=True, text="one"),
        tool_result(ok=True, text="two")
    ])
    stream_ui = FakePlanUI()

    report = asyncio.run(
        build_executor(session, stream_ui).execute_tool_call(
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
    assert stream_ui.events[0][0] == "begin"
    assert stream_ui.events[-1] == ("end", True)


def test_plan_steps_stops_after_failed_step() -> None:
    """步骤失败时按默认策略停止后续计划。"""
    session = FakePlanSession([
        tool_result(ok=False, text="failed"),
        tool_result(ok=True, text="unused")
    ])
    stream_ui = FakePlanUI()

    report = asyncio.run(
        build_executor(session, stream_ui).execute_tool_call(
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
    assert stream_ui.events[0][0] == "begin"
    assert stream_ui.events[-1] == ("end", True)


def test_plan_steps_logs_each_step_start_and_result(monkeypatch) -> None:
    """计划步骤记录可由 reflection 显示的调试日志。"""
    messages: list[str] = []
    monkeypatch.setattr(
        plan_steps_module.logger,
        "debug",
        messages.append
    )
    session = FakePlanSession([tool_result(ok=True, text="done")])

    asyncio.run(
        build_executor(session, FakePlanUI()).execute_tool_call(
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


def test_plan_tool_handler_reuses_standard_display_and_posts_result(monkeypatch) -> None:
    """计划工具复用标准开始和结果展示，并在执行后回传报告。"""
    events: list[tuple[str, object]] = []
    report = SimpleNamespace(ok=True, fields={"ok": True}, text="done")

    class FakeExecutor(object):
        async def execute_tool_call(self, *, arguments: dict) -> object:
            events.append(("execute", arguments))
            return report

    class FakeStreamUI(object):
        async def prepare_external_output(self) -> None:
            events.append(("prepare", None))

    async def fake_show_start(*args, **kwargs) -> None:
        events.append(("start", (args, kwargs)))

    async def fake_show_result(*args, **kwargs) -> None:
        events.append(("result", (args, kwargs)))

    async def fake_post_result(*args, **kwargs) -> dict:
        events.append(("post", (args, kwargs)))
        return {}

    monkeypatch.setattr(plan_call_module, "show_tool_start", fake_show_start)
    monkeypatch.setattr(plan_call_module, "show_tool_result", fake_show_result)
    monkeypatch.setattr(plan_call_module.request, "post_tool_result", fake_post_result)

    runner = PlanToolCallRunner(
        session=SimpleNamespace(),
        stream_ui=FakeStreamUI(),
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
        "prepare", "start", "execute", "result", "post"
    ]
    post_args, _ = events[-1][1]
    assert post_args[3:6] == ("plan_steps", True, {"ok": True})
