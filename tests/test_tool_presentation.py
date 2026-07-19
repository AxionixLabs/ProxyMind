# -*- coding: utf-8 -*-

from mind_app.presentation.rich import (
    render_generic_tool_result_view,
    render_tool_start_view,
)
from mind_app.presentation.tool_views import (
    build_generic_tool_result_view,
    build_tool_start_view,
)


def test_tool_start_view_preserves_current_terminal_shape() -> None:
    """普通工具开始视图保持当前标题、参数预览和记录策略。"""
    arguments = {"path": "/tmp/demo", "count": 2}
    view = build_tool_start_view("sample_tool", arguments)
    rendered = render_tool_start_view(view)

    arguments["count"] = 3

    assert view.name == "sample_tool"
    assert view.arguments == {"path": "/tmp/demo", "count": 2}
    assert view.preview.full == "count=2\npath='/tmp/demo'"
    assert rendered.text == "• Tool sample_tool"
    assert rendered.preserve_display_parts is False
    assert "".join(part["text"] for part in rendered.display_parts) == (
        "• Tool sample_tool\n"
        "└ count=2\n"
        "  path='/tmp/demo'"
    )


def test_generic_tool_result_view_preserves_recorded_preview() -> None:
    """普通工具结果视图保持当前记录缩进和终端样式结构。"""
    view = build_generic_tool_result_view(
        "sample_tool",
        "first line\nsecond line",
        ok=False,
    )
    rendered = render_generic_tool_result_view(view)

    assert view.name == "sample_tool"
    assert view.text == "first line\nsecond line"
    assert view.ok is False
    assert rendered.text == (
        "• Tool sample_tool\n"
        "└ first line\n"
        "  second line"
    )
    assert rendered.preserve_display_parts is True
    assert "".join(part["text"] for part in rendered.display_parts) == (
        "• Tool sample_tool\n"
        "└ first line\n"
        "  second line"
    )

    dot = rendered.display_parts[0]
    assert dot == {"text": "•", "style": "bold #FF6B6B"}
