# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from agent.ports.presentation import TextSpan
from .common import (
    _normalize_preview_lines,
    _trace_preview_from_lines,
)
from .models import TracePreview
from .render import render_tool_trace_parts


def render_generic_tool_result_preview(text: typing.Any) -> TracePreview:
    """把普通工具原始输出转换为仅用于终端显示的预览。"""
    return _trace_preview_from_lines(_normalize_preview_lines(text))


def render_generic_tool_result_parts(
    preview: TracePreview,
    *,
    ok: bool
) -> list[TextSpan]:
    """渲染普通工具结果预览片段；不解析工具内部语义。"""
    return render_tool_trace_parts("", preview=preview, ok=ok)


if __name__ == '__main__':
    pass
