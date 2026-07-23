# -*- coding: utf-8 -*-

from mind_app.presentation.renderers.tool import (
    render_generic_tool_result_view,
    render_tool_start_view,
)
from mind_app.presentation.styles import (
    ACTION_TOOL_CALLED_STYLE,
    ACTION_TOOL_CALLING_STYLE,
    ERROR_DOT_STYLE,
    SUCCESS_DOT_STYLE,
    TOOL_CALLING_DOT_STYLE,
)
from mind_app.presentation.tool_views import (
    build_generic_tool_result_view,
    build_tool_start_view,
)


def _span_style(block, text: str):
    return next(span.style for span in block.spans if span.text == text)


def test_tool_start_uses_calling_copy_and_pending_colors() -> None:
    block = render_tool_start_view(build_tool_start_view(
        "remote_tool",
        {"query": "status"},
    ))

    assert block.plain_text == "• Function Calling remote_tool"
    assert _span_style(block, "•") == TOOL_CALLING_DOT_STYLE
    assert _span_style(block, "Function Calling") == ACTION_TOOL_CALLING_STYLE


def test_tool_result_uses_called_copy_and_result_colors() -> None:
    success = render_generic_tool_result_view(build_generic_tool_result_view(
        "remote_tool",
        "complete",
        ok=True,
    ))
    failure = render_generic_tool_result_view(build_generic_tool_result_view(
        "remote_tool",
        "failed",
        ok=False,
    ))

    assert success.plain_text.startswith("• Function Called remote_tool")
    assert _span_style(success, "•") == SUCCESS_DOT_STYLE
    assert _span_style(success, "Function Called") == ACTION_TOOL_CALLED_STYLE
    assert _span_style(failure, "•") == ERROR_DOT_STYLE
    assert _span_style(failure, "Function Called") == ACTION_TOOL_CALLED_STYLE
