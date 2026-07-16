# -*- coding: utf-8 -*-

import asyncio
from pathlib import Path
from types import SimpleNamespace
from mind_app.client_tools.registry import default_registry
from mind_app.client_tools.types import ClientToolRuntime
from mind_app.client_tools.update_plan import (
    UPDATE_PLAN_INPUT_SCHEMA,
    UPDATE_PLAN_TOOL,
    normalize_update_plan_arguments,
    update_plan_tools
)
from mind_app.runtime.tools.display import (
    show_tool_result,
    show_tool_start
)
from mind_app.runtime.tools.plan_update_display import render_plan_update
from mind_app.stream_state.text import TextState


class FakeStreamUI(object):
    """记录计划展示期间的审计和输出。"""

    def __init__(self) -> None:
        self.audits: list[tuple[str, dict, str | None]] = []
        self.feeds: list[tuple[str, dict]] = []

    def record_tool_arguments(
        self,
        name: str,
        arguments: dict,
        *,
        call_id: str | None = None
    ) -> None:
        self.audits.append((name, arguments, call_id))

    async def feed(self, text: str, **kwargs) -> None:
        self.feeds.append((text, kwargs))

    async def end_status(self) -> None:
        return None


def _valid_plan_data(*, all_completed: bool = False) -> dict:
    """构造计划展示测试使用的结构化数据。"""
    return {
        "explanation": "Update the execution boundary.",
        "plan": [
            {
                "step": "Inspect the current flow",
                "status": "completed" if all_completed else "in_progress"
            },
            {
                "step": "Run regression tests",
                "status": "completed" if all_completed else "pending"
            }
        ]
    }


def test_update_plan_is_registered_as_serial_client_tool(tmp_path: Path) -> None:
    """更新计划工具由默认客户端注册表串行上报。"""
    registry = default_registry(execution_root=tmp_path)
    tool = update_plan_tools()[0]

    assert registry.has_tool(UPDATE_PLAN_TOOL) is True
    assert tool.meta == {
        "hidden": False,
        "domain": "client",
        "class": "plan"
    }
    status_schema = UPDATE_PLAN_INPUT_SCHEMA["properties"]["plan"]["items"]["properties"]["status"]
    assert set(status_schema["enum"]) == {"pending", "in_progress", "completed"}


def test_update_plan_accepts_full_snapshot_without_active_step() -> None:
    """全部待办或全部完成的快照不强制包含进行中步骤。"""
    valid, normalized, errors = normalize_update_plan_arguments({
        "plan": [
            {"step": "First", "status": "pending"},
            {"step": "Second", "status": "pending"}
        ]
    })

    assert valid is True
    assert errors == []
    assert normalized == {
        "explanation": "",
        "plan": [
            {"step": "First", "status": "pending"},
            {"step": "Second", "status": "pending"}
        ]
    }


def test_update_plan_rejects_multiple_active_steps() -> None:
    """同一快照不允许多个步骤同时进行。"""
    valid, _, errors = normalize_update_plan_arguments({
        "plan": [
            {"step": "First", "status": "in_progress"},
            {"step": "Second", "status": "in_progress"}
        ]
    })

    assert valid is False
    assert errors == ["multiple in_progress steps"]


def test_update_plan_handler_returns_normalized_english_result() -> None:
    """工具处理器返回标准化数据和英文摘要。"""
    tool = update_plan_tools()[0]
    result = asyncio.run(tool.handler(
        {
            "explanation": "  Final verification.  ",
            "plan": [{"step": "  Run tests  ", "status": "completed"}]
        },
        ClientToolRuntime(session=None)
    ))
    structured = result.structuredContent or {}

    assert result.isError is False
    assert structured["text"].endswith(
        "plan updated steps=1 completed=1 in_progress=0 pending=0"
    )
    assert structured["data"]["explanation"] == "Final verification."
    assert structured["data"]["plan"] == [
        {"step": "Run tests", "status": "completed"}
    ]


def test_completed_plan_body_is_dim_but_title_is_not() -> None:
    """全部完成时摘要和步骤使用不同 dim 色，标题保持正常强调。"""
    rendered = render_plan_update(_valid_plan_data(all_completed=True))

    assert rendered is not None
    text, parts = rendered
    assert text == (
        "• Updated Plan\n"
        "  └ Update the execution boundary.\n"
        "    ✔ Inspect the current flow\n"
        "    ✔ Run regression tests"
    )

    dot_part = next(part for part in parts if part["text"] == "•")
    title_part = next(part for part in parts if part["text"] == " Updated Plan")
    summary_part = next(
        part for part in parts
        if part["text"] == "Update the execution boundary."
    )
    step_parts = [
        part for part in parts
        if part["text"] in {"Inspect the current flow", "Run regression tests"}
    ]
    assert dot_part["style"] == "bold #6EE7A8"
    assert title_part["style"] == "bold #D7E7FF"
    assert "dim" in str(summary_part["style"])
    assert step_parts
    assert all("dim" in str(part["style"]) for part in step_parts)
    assert all(part["style"] != summary_part["style"] for part in step_parts)


def test_incomplete_plan_dims_done_summary_and_pending_then_highlights_active() -> None:
    """计划执行中仅高亮当前步骤，摘要弱于其他非活动内容。"""
    rendered = render_plan_update({
        "explanation": "Continue implementation.",
        "plan": [
            {"step": "Completed step", "status": "completed"},
            {"step": "Active step", "status": "in_progress"},
            {"step": "Pending step", "status": "pending"}
        ]
    })

    assert rendered is not None
    _, parts = rendered
    styles = {
        str(part["text"]): str(part["style"])
        for part in parts
        if part["text"] in {
            "Continue implementation.",
            "Completed step",
            "Active step",
            "Pending step"
        }
    }
    assert styles["Continue implementation."] == "dim #7E8FA3"
    assert styles["Completed step"] == "dim #A5B3C2"
    assert styles["Active step"] == "#7DD3FC"
    assert styles["Pending step"] == "dim #A5B3C2"


def test_update_plan_uses_special_display_and_preserves_audit() -> None:
    """更新计划不显示通用工具起始轨迹，但保留参数审计。"""
    stream_ui = FakeStreamUI()
    arguments = {"plan": [{"step": "Run tests", "status": "completed"}]}

    asyncio.run(show_tool_start(
        stream_ui,
        UPDATE_PLAN_TOOL,
        arguments,
        call_id="call-1"
    ))

    assert stream_ui.audits == [(UPDATE_PLAN_TOOL, arguments, "call-1")]
    assert stream_ui.feeds == []

    tool_run = SimpleNamespace(
        ok=True,
        fields={},
        text="plan updated",
        data=_valid_plan_data(),
        cost_ms=0
    )
    asyncio.run(show_tool_result(
        stream_ui,
        UPDATE_PLAN_TOOL,
        arguments,
        tool_run
    ))

    assert len(stream_ui.feeds) == 1
    text, kwargs = stream_ui.feeds[0]
    assert text.startswith("• Updated Plan")
    assert kwargs["display"] == TextState.BLOCK
    assert kwargs["preserve_display_parts"] is True


def test_update_plan_failure_uses_generic_tool_result() -> None:
    """计划校验失败时保留通用工具错误轨迹。"""
    stream_ui = FakeStreamUI()
    tool_run = SimpleNamespace(
        ok=False,
        fields={},
        text="plan rejected errors=empty plan",
        data={"plan": [], "explanation": ""},
        cost_ms=0
    )

    asyncio.run(show_tool_result(
        stream_ui,
        UPDATE_PLAN_TOOL,
        {"plan": []},
        tool_run
    ))

    assert len(stream_ui.feeds) == 1
    assert stream_ui.feeds[0][0].startswith("• Tool update_plan")


def test_update_plan_block_follows_global_stream_spacing() -> None:
    """计划块在前后流式正文之间各保留一个视觉空行。"""
    rendered = render_plan_update(_valid_plan_data())
    assert rendered is not None
    plan_text, plan_parts = rendered

    state = TextState()
    state.append("Before", display=TextState.STREAM)
    state.append(
        plan_text,
        display=TextState.BLOCK,
        display_parts=plan_parts,
        preserve_display_parts=True
    )
    state.append("After", display=TextState.STREAM)

    assert state.display_text == (
        "Before\n\n"
        "• Updated Plan\n"
        "  └ Update the execution boundary.\n"
        "    □ Inspect the current flow\n"
        "    □ Run regression tests\n\n"
        "After"
    )


if __name__ == '__main__':
    pass
