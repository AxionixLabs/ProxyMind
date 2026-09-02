# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from agent.application.views.patch import PatchView
from agent.application.views.tools import (
    GenericToolResultView,
    NativeToolResultView,
    ToolStartView,
)
from .patch import (
    build_patch_result_view,
    build_patch_start_view,
)


def build_tool_start_view(
    name: str,
    arguments: dict[str, typing.Any],
    *,
    patch_preview: dict[str, typing.Any] | None = None,
    call_id: str = "",
) -> ToolStartView | PatchView:
    """把工具调用转换为不包含前端渲染数据的开始视图。"""
    normalized_arguments = dict(arguments) if isinstance(arguments, dict) else {}
    normalized_name = str(name or "tool").strip() or "tool"

    if normalized_name == "apply_patch":
        return build_patch_start_view(
            normalized_arguments,
            preview_data=patch_preview,
            call_id=call_id,
        )

    return ToolStartView(
        name=normalized_name,
        arguments=normalized_arguments,
        call_id=str(call_id or ""),
    )


def build_generic_tool_result_view(
    name: str,
    text: typing.Any,
    *,
    ok: bool,
    call_id: str = "",
) -> GenericToolResultView:
    """把普通工具结果转换为不包含前端渲染数据的结果视图。"""
    normalized_name = str(name or "tool").strip() or "tool"
    normalized_text = str(text or "")
    if normalized_name == "view_image":
        normalized_text = _view_image_result_text(normalized_text, ok=bool(ok))

    return GenericToolResultView(
        name=normalized_name,
        text=normalized_text,
        ok=bool(ok),
        call_id=str(call_id or ""),
    )


def build_native_tool_result_view(
    name: str,
    arguments: dict[str, typing.Any],
    *,
    ok: bool,
    data: typing.Any = None,
    cost_ms: int | None = None,
    call_id: str = "",
) -> NativeToolResultView | PatchView:
    """把原生工具结果转换为不包含前端渲染数据的结果视图。"""
    normalized_name = str(name or "tool").strip() or "tool"
    normalized_arguments = dict(arguments) if isinstance(arguments, dict) else {}
    normalized_data = dict(data) if isinstance(data, dict) else data
    normalized_cost_ms = _normalized_cost_ms(cost_ms)

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
        call_id=str(call_id or ""),
    )


def _view_image_result_text(text: str, *, ok: bool) -> str:
    """提取图片查看结果中适合跨前端消费的内容。"""
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


def _normalized_cost_ms(value: int | None) -> int | None:
    """规范化可选的非负毫秒耗时。"""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return max(0, value)


if __name__ == '__main__':
    pass
