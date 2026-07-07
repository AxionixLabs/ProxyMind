# -*- coding: utf-8 -*-

from mind_app.stream_events.tool_traces.native import render_tool_result_preview


def test_shell_command_preview_uses_ordered_output_lines() -> None:
    """shell_command 预览优先使用接收顺序输出。"""
    preview = render_tool_result_preview(
        "shell_command",
        {
            "stdout": "stdout-second\n",
            "stderr": "stderr-first\n",
            "output_lines": ["stderr-first", "stdout-second"],
            "exit_code": 0,
            "timed_out": False,
        },
        ok=True,
    )

    assert preview.screen.splitlines() == ["stderr-first", "stdout-second"]


def test_shell_command_error_preview_uses_ordered_output_lines() -> None:
    """失败预览也保留接收顺序输出。"""
    preview = render_tool_result_preview(
        "shell_command",
        {
            "stdout": "stdout-second\n",
            "stderr": "stderr-first\n",
            "output_lines": ["stderr-first", "stdout-second"],
            "exit_code": 1,
            "timed_out": False,
        },
        ok=False,
    )

    assert preview.screen.splitlines() == ["stderr-first", "stdout-second"]

