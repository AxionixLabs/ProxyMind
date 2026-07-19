# -*- coding: utf-8 -*-

import asyncio

import pytest

from mind_app.presentation.rich import render_native_tool_result_view
from mind_app.presentation.legacy import LegacyPresentationSink
from mind_app.presentation.tool_views import build_native_tool_result_view
from mind_app.runtime.tools.display import show_tool_result
from mind_app.runtime.tools.run import ToolRunResult


class FakeOutput(object):
    """记录 native 工具展示产生的状态和输出。"""

    def __init__(self) -> None:
        self.end_count = 0
        self.feeds: list[tuple[str, dict]] = []

    async def end_status(self, *, immediate: bool = False) -> None:
        _ = immediate
        self.end_count += 1

    async def feed(self, text: str, **kwargs) -> None:
        self.feeds.append((text, kwargs))


@pytest.mark.parametrize(
    ("name", "arguments", "data", "title", "preview_kind"),
    (
        (
            "shell_command",
            {"command": "printf hello"},
            {
                "command": "printf hello",
                "output_lines": ["hello"],
                "exit_code": 0,
            },
            "• Ran printf hello",
            "plain",
        ),
        (
            "exec_command",
            {"command": "pytest -q"},
            {
                "command": "pytest -q",
                "status": "running",
                "session_id": "session-1",
            },
            "• Started pytest -q",
            "plain",
        ),
        (
            "write_stdin",
            {"session_id": "session-1", "chars": "x"},
            {
                "session_id": "session-1",
                "output_lines": ["done"],
            },
            "• Wrote stdin session-1",
            "plain",
        ),
        (
            "apply_patch",
            {
                "patch": (
                    "*** Begin Patch\n"
                    "*** Update File: demo.py\n"
                    "@@\n"
                    "-old\n"
                    "+new\n"
                    "*** End Patch"
                ),
            },
            {
                "files": [{
                    "path": "demo.py",
                    "action": "modify",
                    "added_lines": 1,
                    "removed_lines": 1,
                }],
                "added_lines": 1,
                "removed_lines": 1,
            },
            "• Edited demo.py (+1 -1)",
            "patch_tree",
        ),
    ),
)
def test_native_tool_view_preserves_tool_specific_structure(
    name: str,
    arguments: dict,
    data: dict,
    title: str,
    preview_kind: str,
) -> None:
    """native View 保留各工具的标题、参数、结果和预览类型。"""
    view = build_native_tool_result_view(
        name,
        arguments,
        ok=True,
        data=data,
        cost_ms=12,
    )

    arguments.clear()
    data["changed"] = True

    assert view.name == name
    assert view.arguments
    assert "changed" not in view.data
    assert view.ok is True
    assert view.cost_ms == 12
    assert len(view.entries) == 1
    assert view.entries[0].title == title
    assert view.entries[0].preview.kind == preview_kind


def test_native_shell_error_keeps_diagnostic_styles() -> None:
    """shell 失败 View 继续使用错误诊断解析和样式。"""
    view = build_native_tool_result_view(
        "shell_command",
        {"command": "python demo.py"},
        ok=False,
        data={
            "command": "python demo.py",
            "output_lines": [
                "Traceback (most recent call last):",
                "ValueError: invalid value",
            ],
            "exit_code": 1,
        },
    )
    rendered = render_native_tool_result_view(view, terminal_width=100)[0]

    assert rendered.text == (
        "• Ran python demo.py\n"
        "└ Traceback (most recent call last):\n"
        "  ValueError: invalid value"
    )
    assert any(
        part["style"] == "dim #C66A6A"
        for part in rendered.display_parts
    )
    assert rendered.preserve_display_parts is True


def test_native_patch_keeps_diff_and_code_highlighting() -> None:
    """apply_patch View 继续保留文件树、增删行和代码 token 样式。"""
    patch = (
        "*** Begin Patch\n"
        "*** Update File: demo.py\n"
        "@@\n"
        "-value = 'old'\n"
        "+value = 'new'\n"
        "*** End Patch"
    )
    view = build_native_tool_result_view(
        "apply_patch",
        {"patch": patch},
        ok=True,
        data={
            "files": [{
                "path": "demo.py",
                "action": "modify",
                "added_lines": 1,
                "removed_lines": 1,
            }],
            "added_lines": 1,
            "removed_lines": 1,
        },
    )
    rendered = render_native_tool_result_view(view, terminal_width=100)[0]
    styles = {str(part["style"]) for part in rendered.display_parts}

    assert "└─ demo.py (+1 -1)" in view.entries[0].preview.full
    assert "bold #6EE7A8" in styles
    assert "dim #FF8A8A" in styles
    assert "#A9CDBB" in styles


def test_show_native_tool_result_uses_view_adapter() -> None:
    """native 工具结果通过 View 适配器输出且不控制运行状态。"""
    output = FakeOutput()
    presentation = LegacyPresentationSink(output)
    tool_run = ToolRunResult(
        result={},
        ok=True,
        fields={},
        text="done",
        data={
            "command": "printf hello",
            "output_lines": ["hello"],
            "exit_code": 0,
        },
        cost_ms=5,
    )

    asyncio.run(show_tool_result(
        presentation,
        "shell_command",
        {"command": "printf hello"},
        tool_run,
        use_coding_trace=True,
    ))

    assert output.end_count == 0
    assert len(output.feeds) == 1
    assert output.feeds[0][0] == "• Ran printf hello\n└ hello"
    assert output.feeds[0][1]["preserve_display_parts"] is True
