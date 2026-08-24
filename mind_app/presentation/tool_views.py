# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_app.stream_events.tool_traces.generic import render_generic_tool_result_preview
from mind_app.stream_events.tool_traces.native import (
    render_tool_result_entries,
    render_tool_start_preview,
    render_tool_start_trace
)
from .models import (
    GenericToolResultView,
    NativeToolResultView,
    PatchView,
    ToolStartView
)
from .patch_views import (
    build_patch_result_view,
    build_patch_start_view
)


def build_tool_start_view(
    name: str,
    arguments: dict[str, typing.Any],
    *,
    call_id: str = ""
) -> ToolStartView | PatchView:
    """构建普通工具开始执行时的展示数据。"""
    normalized_arguments = dict(arguments) if isinstance(arguments, dict) else {}

    normalized_name = str(name or "tool").strip() or "tool"
    if normalized_name == "apply_patch":
        return build_patch_start_view(
            normalized_arguments,
            call_id=call_id,
        )

    return ToolStartView(
        name=normalized_name,
        arguments=normalized_arguments,
        title=render_tool_start_trace(name, normalized_arguments),
        preview=render_tool_start_preview(
            normalized_arguments,
            name=str(name or "").strip(),
        ),
        call_id=str(call_id or ""),
    )


def build_generic_tool_result_view(
    name: str,
    text: typing.Any,
    *,
    ok: bool,
    call_id: str = ""
) -> GenericToolResultView:
    """构建普通工具执行结果的展示数据。"""
    normalized_name = str(name or "tool").strip() or "tool"
    normalized_text = str(text or "")
    title = f"• Function Invoked {normalized_name}"

    if normalized_name == "view_image":
        normalized_text = _view_image_result_text(normalized_text, ok=bool(ok))
        title = "• Viewed"

    return GenericToolResultView(
        name=normalized_name,
        text=normalized_text,
        ok=bool(ok),
        title=title,
        preview=render_generic_tool_result_preview(normalized_text),
        call_id=str(call_id or ""),
    )


def _view_image_result_text(text: str, *, ok: bool) -> str:
    """提取图片查看结果中适合直接展示的内容。"""
    metadata = f"tool=view_image source=client ok={ok}"
    if text == metadata:
        result = ""
    elif text.startswith(f"{metadata} "):
        result = text[len(metadata) + 1:]
    else:
        return text

    loaded_prefix = "Loaded image: "
    if ok and result.startswith(loaded_prefix):
        return result[len(loaded_prefix):]
    return result


def build_native_tool_result_view(
    name: str,
    arguments: dict[str, typing.Any],
    *,
    ok: bool,
    data: typing.Any = None,
    cost_ms: int | None = None,
    call_id: str = "",
) -> NativeToolResultView | PatchView:
    """构建原生编码工具执行结果的展示数据。"""
    normalized_name      = str(name or "tool").strip() or "tool"
    normalized_arguments = dict(arguments) if isinstance(arguments, dict) else {}
    normalized_data      = dict(data) if isinstance(data, dict) else data
    normalized_cost_ms   = _normalized_cost_ms(cost_ms)

    if normalized_name == "apply_patch":
        return build_patch_result_view(
            normalized_arguments,
            ok=bool(ok),
            data=normalized_data,
            cost_ms=normalized_cost_ms,
            call_id=call_id,
        )

    return NativeToolResultView(
        name=normalized_name,
        arguments=normalized_arguments,
        ok=bool(ok),
        data=normalized_data,
        cost_ms=normalized_cost_ms,
        entries=tuple(render_tool_result_entries(
            normalized_name,
            normalized_arguments,
            ok=bool(ok),
            data=normalized_data,
            cost_ms=normalized_cost_ms,
        )),
        call_id=str(call_id or ""),
    )


def _normalized_cost_ms(value: int | None) -> int | None:
    """规范化可选的非负毫秒耗时。"""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return max(0, value)


if __name__ == '__main__':
    pass
