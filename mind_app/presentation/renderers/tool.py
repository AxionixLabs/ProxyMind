# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import typing
from mind_app.stream_events.command_preview import command_text
from mind_app.stream_events.tool_traces.native import (
    render_tool_start_trace,
    render_tool_trace
)
from mind_app.stream_events.tool_traces.render import render_tool_trace_parts
from mind_app.stream_events.tool_policy import (
    ToolDisplayKind,
    tool_display_spec
)
from ..formatting import format_duration_ms
from ..models import (
    GenericToolResultView,
    NativeToolResultView,
    StyledBlock,
    TextSpan,
    TextStyle,
    ToolStartView,
    TracePreview
)

TRANSCRIPT_SUCCESS_STYLE  = TextStyle(foreground="#6EE7A8", bold=True)
TRANSCRIPT_FAILURE_STYLE  = TextStyle(foreground="#FF6B6B", bold=True)
TRANSCRIPT_DURATION_STYLE = TextStyle(dim=True)


def render_tool_start_view(
    view: ToolStartView,
    *,
    terminal_width: int | None = None,
    measure_width: typing.Callable[[str], int] | None = None,
) -> StyledBlock:
    """把普通工具开始视图转换为中立展示块。"""
    title = (
        render_tool_start_trace(
            view.name,
            view.arguments,
            terminal_width=terminal_width,
            measure_width=measure_width,
        )
        if view.name in {"shell_command", "exec_command"}
        else view.title
    )
    return StyledBlock(
        plain_text=title,
        spans=tuple(render_tool_trace_parts(
            title,
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
    if tool_display_spec(view.name).kind is ToolDisplayKind.JAVASCRIPT:
        return (render_javascript_result_view(
            view,
            terminal_width=terminal_width,
            measure_width=measure_width,
        ),)

    blocks: list[StyledBlock] = []
    for entry in view.entries:
        title = _native_result_title(
            view,
            entry.title,
            terminal_width=terminal_width,
            measure_width=measure_width,
        )
        blocks.append(StyledBlock(
            plain_text=_coding_trace_text(
                title,
                entry.preview,
            ),
            spans=tuple(render_tool_trace_parts(
                title,
                preview=entry.preview,
                ok=entry.ok,
                terminal_width=(
                    terminal_width
                    if view.name in {"shell_command", "exec_command", "write_stdin"}
                    else None
                ),
                measure_width=measure_width,
            )),
            preserve_spans=True,
        ))
    return tuple(blocks)


def render_javascript_result_view(
    view: NativeToolResultView,
    *,
    terminal_width: int | None = None,
    measure_width: typing.Callable[[str], int] | None = None
) -> StyledBlock:
    """把 JavaScript 执行结果转换为完成态展示块。"""
    entry          = view.entries[0] if view.entries else None
    title          = entry.title if entry is not None else "• JavaScript"
    result_preview = entry.preview if entry is not None else TracePreview()

    return StyledBlock(
        plain_text=_coding_trace_text(title, result_preview),
        spans=tuple(render_tool_trace_parts(
            title,
            preview=result_preview,
            ok=view.ok,
        )),
        preserve_spans=True,
    )


def _native_result_title(
    view: NativeToolResultView,
    fallback: str,
    *,
    terminal_width: int | None,
    measure_width: typing.Callable[[str], int] | None
) -> str:
    """返回按当前终端宽度生成的原生工具结果标题。"""
    if view.name not in {"shell_command", "exec_command"}:
        return fallback
    return render_tool_trace(
        view.name,
        view.arguments,
        ok=view.ok,
        data=view.data,
        cost_ms=view.cost_ms,
        terminal_width=terminal_width,
        measure_width=measure_width,
    )


def render_javascript_result_transcript_view(view: NativeToolResultView) -> StyledBlock:
    """把 JavaScript 执行结果转换为完整记录块。"""
    title  = view.entries[0].title if view.entries else "• JavaScript"
    output = _javascript_result_text(view)

    return StyledBlock(
        plain_text=_javascript_completed_text(title, output),
    )


def render_javascript_result_raw_text(view: NativeToolResultView) -> str:
    """返回 JavaScript 执行结果的不含视觉装饰文本。"""
    return _javascript_result_text(view)


def render_tool_start_transcript_view(view: ToolStartView) -> StyledBlock:
    """把工具启动信息转换为完整记录块。"""
    if view.name in {"shell_command", "exec_command"}:
        command = _command_text(view.arguments.get("command"))
        return _transcript_block(view.title, f"$ {command}" if command else "")
    if view.name == "write_stdin":
        return _transcript_block(view.title, _json_text(view.arguments))
    if view.name == "apply_patch":
        return _transcript_block(view.title, str(view.arguments.get("patch") or ""))

    if tool_display_spec(view.name).kind is ToolDisplayKind.JAVASCRIPT:
        source_field = tool_display_spec(view.name).source_field
        source       = view.arguments.get(source_field) if source_field else ""

        return _transcript_block(view.title, str(source or ""))

    return _transcript_block(view.title, _json_text(view.arguments))


def render_generic_tool_result_transcript_view(view: GenericToolResultView) -> StyledBlock:
    """把普通工具结果转换为完整记录块。"""
    return _transcript_block(view.title, view.text)


def render_native_tool_result_transcript_view(view: NativeToolResultView) -> tuple[StyledBlock, ...]:
    """把原生编码工具结果转换为完整记录块。"""
    if tool_display_spec(view.name).kind is ToolDisplayKind.JAVASCRIPT:
        return (render_javascript_result_transcript_view(view),)

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

        return (_command_result_transcript_block(
            title,
            body,
            ok=view.ok,
            cost_ms=view.cost_ms,
            exit_code=payload.get("exit_code"),
        ),)

    if view.name == "write_stdin":
        output = _native_output_text(payload)
        body = output or _json_text(view.arguments)
        return (_transcript_block(title, body),)

    if view.name == "apply_patch":
        patch = str(view.arguments.get("patch") or "")
        detail = patch or _json_text(payload)
        return (_transcript_block(title, detail),)

    return (_transcript_block(title, _json_text(payload or view.data)),)


def render_tool_start_raw_text(view: ToolStartView) -> str:
    """把工具启动信息转换为无装饰文本。"""
    if view.name in {"shell_command", "exec_command"}:
        return _command_text(view.arguments.get("command"))
    if view.name == "apply_patch":
        return str(view.arguments.get("patch") or "")
    if tool_display_spec(view.name).kind is ToolDisplayKind.JAVASCRIPT:
        source_field = tool_display_spec(view.name).source_field
        return str(view.arguments.get(source_field) or "") if source_field else ""

    return _json_text(view.arguments)


def render_generic_tool_result_raw_text(view: GenericToolResultView) -> str:
    """把普通工具结果转换为无装饰文本。"""
    return str(view.text or "")


def render_native_tool_result_raw_text(view: NativeToolResultView) -> tuple[str, ...]:
    """把原生工具结果转换为无装饰文本块。"""
    if tool_display_spec(view.name).kind is ToolDisplayKind.JAVASCRIPT:
        return (render_javascript_result_raw_text(view),)

    payload = _native_payload(view.data)

    if view.name in {"shell_command", "exec_command"}:
        command = _command_text(
            payload.get("command") or view.arguments.get("command")
        )
        output = _native_output_text(payload)
        return ("\n".join(item for item in (command, output) if item),)

    if view.name == "write_stdin":
        return (_native_output_text(payload) or _json_text(view.arguments),)

    if view.name == "apply_patch":
        patch = str(view.arguments.get("patch") or "")
        return (patch or _json_text(payload),)

    return (_json_text(payload or view.data),)


def _transcript_block(title: str, body: str) -> StyledBlock:
    """生成不截断正文内容的记录块。"""
    heading = str(title or "").rstrip()
    content = str(body or "").strip("\n")
    text    = f"{heading}\n{content}" if heading and content else heading or content

    return StyledBlock(plain_text=text)


def _command_result_transcript_block(
    title: str,
    body: str,
    *,
    ok: bool,
    cost_ms: int | None,
    exit_code: typing.Any
) -> StyledBlock:
    """在完整命令输出底部追加静态执行结果。"""
    block = _transcript_block(title, body)
    if cost_ms is None:
        return block

    icon = "✓" if ok else "✗"

    icon_style = (
        TRANSCRIPT_SUCCESS_STYLE if ok else TRANSCRIPT_FAILURE_STYLE
    )

    code = (
        exit_code
        if not ok and isinstance(exit_code, int) and not isinstance(exit_code, bool)
        else None
    )

    status_spans = [TextSpan(icon, icon_style)]

    if code is not None:
        status_spans.append(TextSpan(f" ({code})"))
    status_spans.append(TextSpan(
        f" • {format_duration_ms(cost_ms)}",
        TRANSCRIPT_DURATION_STYLE,
    ))

    separator   = "\n" if block.plain_text else ""
    status_text = "".join(span.text for span in status_spans)

    return StyledBlock(
        plain_text=f"{block.plain_text}{separator}{status_text}",
        spans=(
            *((TextSpan(f"{block.plain_text}{separator}"),) if separator else ()),
            *status_spans,
        ),
    )


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


def _javascript_result_text(view: NativeToolResultView) -> str:
    """返回 JavaScript 执行保留的完整输出。"""
    payload = _native_payload(view.data)
    value   = payload.get("output") if view.ok else payload.get("error")

    if value not in (None, ""):
        if isinstance(value, str):
            return value.strip("\n")
        return _json_text(value)

    return "JavaScript cell completed." if view.ok else "JavaScript cell failed."


def _javascript_completed_text(title: str, output: str) -> str:
    """生成 JavaScript 执行结果的纯文本表示。"""
    lines = [str(title or "").rstrip()]
    output_lines = str(output or "").split("\n") if output else []

    if output_lines:
        lines.append(f"└ {output_lines[0]}")
        lines.extend(f"  {line}" for line in output_lines[1:])

    return "\n".join(line for line in lines if line or len(lines) > 1)


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
