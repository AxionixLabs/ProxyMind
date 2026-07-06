# -*- coding: utf-8 -*-

from mind_app.stream_events.tool_traces.common import (
    ACTION_RUN_STYLE,
    ACTION_TOOL_STYLE,
    COMMAND_HEAD_STYLE,
    COMMAND_STYLE,
    TITLE_STYLE,
)
from mind_app.stream_events.tool_traces.render import render_tool_trace_parts


def joined_text(parts: list[dict[str, str | None]]) -> str:
    """拼回分段文本。"""
    return "".join(str(part.get("text") or "") for part in parts)


def test_started_command_title_colors_prefix_and_command() -> None:
    """Started 标题会把动作前缀和命令 token 分开着色。"""
    parts = render_tool_trace_parts("• Started adb logcat")

    assert joined_text(parts) == "• Started adb logcat"
    assert {"text": "Started", "style": ACTION_RUN_STYLE} in parts
    assert {"text": "adb", "style": COMMAND_HEAD_STYLE} in parts
    assert {"text": "logcat", "style": COMMAND_STYLE} in parts


def test_wrote_stdin_title_colors_multi_word_prefix() -> None:
    """Wrote stdin 标题会把双词动作前缀单独着色。"""
    parts = render_tool_trace_parts("• Wrote stdin exec_2f844055833eeeab")

    assert joined_text(parts) == "• Wrote stdin exec_2f844055833eeeab"
    assert {"text": "Wrote stdin", "style": ACTION_TOOL_STYLE} in parts
    assert {"text": " exec_2f844055833eeeab", "style": TITLE_STYLE} in parts
