# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import typing
from mind_app.stream_events.command_preview import command_text
from mind_app.stream_events.tool_traces.render import render_tool_trace_parts
from ..models import (
    GenericToolResultView,
    NativeToolResultView,
    StyledBlock,
    ToolStartView,
    TracePreview
)


def render_tool_start_view(view: ToolStartView) -> StyledBlock:
    """把普通工具开始视图转换为中立展示块。"""
    return StyledBlock(
        plain_text=view.title,
        spans=tuple(render_tool_trace_parts(
            view.title,
            preview=view.preview,
            ok=None,
        )),
    )


def render_generic_tool_result_view(view: GenericToolResultView) -> StyledBlock:
    """把普通工具结果视图转换为中立展示块。"""
    return StyledBlock(
        plain_text=_generic_trace_text(view.title, view.preview),
        spans=tuple(render_tool_trace_parts(
            view.title,
            preview=view.preview,
            ok=view.ok,
        )),
        preserve_spans=True,
    )


def render_native_tool_result_view(
    view: NativeToolResultView,
    *,
    terminal_width: int | None = None,
    measure_width: typing.Callable[[str], int] | None = None,
) -> tuple[StyledBlock, ...]:
    """把原生编码工具结果视图转换为中立展示块。"""
    return tuple(
        StyledBlock(
            plain_text=_coding_trace_text(entry.title, entry.preview),
            spans=tuple(render_tool_trace_parts(
                entry.title,
                preview=entry.preview,
                ok=entry.ok,
                terminal_width=terminal_width,
                measure_width=measure_width,
            )),
            preserve_spans=True,
        )
        for entry in view.entries
    )


def render_tool_start_transcript_view(view: ToolStartView) -> StyledBlock:
    """把工具启动信息转换为完整记录块。"""
    if view.name in {"shell_command", "exec_command"}:
        command = _command_text(view.arguments.get("command"))
        return _transcript_block(view.title, f"$ {command}" if command else "")
    if view.name == "write_stdin":
        return _transcript_block(view.title, _json_text(view.arguments))
    if view.name == "apply_patch":
        return _transcript_block(view.title, str(view.arguments.get("patch") or ""))

    return _transcript_block(view.title, _json_text(view.arguments))


def render_generic_tool_result_transcript_view(
    view: GenericToolResultView
) -> StyledBlock:
    """把普通工具结果转换为完整记录块。"""
    return _transcript_block(view.title, view.text)


def render_native_tool_result_transcript_view(
    view: NativeToolResultView
) -> tuple[StyledBlock, ...]:
    """把原生编码工具结果转换为完整记录块。"""
    payload = _native_payload(view.data)
    title   = view.entries[0].title if view.entries else f"• Ran {view.name}"

    if view.name in {"shell_command", "exec_command"}:
        command = _command_text(
            payload.get("command") or view.arguments.get("command")
        )
        output = _native_output_text(payload)
        body = "\n".join(
            item for item in (f"$ {command}" if command else "", output) if item
        )
        return (_transcript_block(title, body),)

    if view.name == "write_stdin":
        output = _native_output_text(payload)
        body = output or _json_text(view.arguments)
        return (_transcript_block(title, body),)

    if view.name == "apply_patch":
        patch = str(view.arguments.get("patch") or "")
        detail = patch or _json_text(payload)
        return (_transcript_block(title, detail),)

    return (_transcript_block(title, _json_text(payload or view.data)),)


def _transcript_block(title: str, body: str) -> StyledBlock:
    """生成不截断正文内容的记录块。"""
    heading = str(title or "").rstrip()
    content = str(body or "").strip("\n")
    text    = f"{heading}\n{content}" if heading and content else heading or content

    return StyledBlock(plain_text=text)


def _command_text(value: typing.Any) -> str:
    """返回保留换行的完整命令文本。"""
    if isinstance(value, list):
        return command_text(value)
    return str(value or "").strip()


def _native_payload(value: typing.Any) -> dict[str, typing.Any]:
    """提取原生工具结果中的结构化载荷。"""
    if not isinstance(value, dict):
        return {}

    results = value.get("results")

    if isinstance(results, list):
        for item in results:
            if isinstance(item, dict) and isinstance(item.get("data"), dict):
                return dict(item["data"])

    return dict(value)


def _native_output_text(payload: dict[str, typing.Any]) -> str:
    """按接收顺序返回原生命令已经保留的完整输出。"""
    output_lines = payload.get("output_lines")
    if isinstance(output_lines, (list, tuple)) and output_lines:
        return "\n".join(str(item or "").rstrip("\n") for item in output_lines)

    output = payload.get("output")
    if output is not None:
        return str(output)

    stdout = str(payload.get("stdout") or "")
    stderr = str(payload.get("stderr") or "")

    return f"{stdout}{stderr}"


def _json_text(value: typing.Any) -> str:
    """把结构化值转换为稳定的完整文本。"""
    if value in (None, {}, [], ()):
        return ""

    return json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        default=str,
    )


def _generic_trace_text(title: str, preview: TracePreview) -> str:
    """生成普通工具结果的记录文本。"""
    if not preview.full:
        return title

    indented_preview = preview.full.replace("\n", "\n  ")
    return f"{title}\n└ {indented_preview}"


def _coding_trace_text(title: str, preview: TracePreview) -> str:
    """生成原生编码工具结果的记录文本。"""
    if not preview.full:
        return title
    if preview.kind == "tree":
        return f"{title}\n{preview.full}"

    indented_preview = preview.full.replace("\n", "\n  ")
    return f"{title}\n└ {indented_preview}"


if __name__ == '__main__':
    pass
