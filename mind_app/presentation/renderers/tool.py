# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import typing
from mind_app.presentation.stream.command_preview import command_text
from mind_app.presentation.stream.tool_traces.native import (
    render_tool_start_trace,
    render_tool_trace
)
from mind_app.presentation.stream.tool_traces.render import render_tool_trace_parts
from ..tool_policy import (
    ToolDisplayKind,
    tool_display_spec
)
from ..formatting import format_duration_ms
from ..text_layout import (
    clip_display_text,
    text_display_width,
)
from ..terminal_text import sanitize_terminal_line
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
    measure_width: typing.Callable[[str], int] | None = None
) -> StyledBlock:
    """把普通工具开始视图转换为中立展示块。"""
    spec = tool_display_spec(view.name)

    title = _display_title(
        (
            render_tool_start_trace(
                view.name,
                view.arguments,
                terminal_width=terminal_width,
                measure_width=measure_width,
            )
            if spec.kind is ToolDisplayKind.SHELL
            else view.title
        ),
        terminal_width=terminal_width,
        measure_width=measure_width,
    )

    spans = tuple(render_tool_trace_parts(
        title,
        preview=view.preview,
        ok=None,
        terminal_width=terminal_width,
        measure_width=measure_width,
    ))

    return StyledBlock(
        plain_text=(
            "".join(span.text for span in spans)
            if isinstance(terminal_width, int) and terminal_width > 0
            else title
        ),
        spans=spans,
    )


def render_generic_tool_result_view(
    view: GenericToolResultView,
    *,
    terminal_width: int | None = None,
    measure_width: typing.Callable[[str], int] | None = None,
) -> StyledBlock:
    """把普通工具结果视图转换为中立展示块。"""
    title = _display_title(
        view.title,
        terminal_width=terminal_width,
        measure_width=measure_width,
    )
    spans = tuple(render_tool_trace_parts(
        title,
        preview=view.preview,
        ok=view.ok,
        terminal_width=terminal_width,
        measure_width=measure_width,
    ))
    return StyledBlock(
        plain_text=(
            "".join(span.text for span in spans)
            if isinstance(terminal_width, int) and terminal_width > 0
            else _generic_trace_text(title, view.preview)
        ),
        spans=spans,
        preserve_spans=True,
    )


def render_native_tool_result_view(
    view: NativeToolResultView,
    *,
    terminal_width: int | None = None,
    measure_width: typing.Callable[[str], int] | None = None
) -> tuple[StyledBlock, ...]:
    """把原生编码工具结果视图转换为中立展示块。"""
    spec = tool_display_spec(view.name)
    if spec.kind is ToolDisplayKind.JAVASCRIPT:
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
                terminal_width=(terminal_width if spec.kind in {
                    ToolDisplayKind.SHELL,
                    ToolDisplayKind.STDIN,
                } else None),
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
            terminal_width=terminal_width,
            measure_width=measure_width,
        )),
        preserve_spans=True,
    )


def render_javascript_result_transcript_view(view: NativeToolResultView) -> StyledBlock:
    """把 JavaScript 执行结果转换为完整记录块。"""
    title  = view.entries[0].title if view.entries else "• JavaScript"
    output = _javascript_result_text(view)

    return _transcript_block(title, output)


def render_javascript_result_raw_text(view: NativeToolResultView) -> str:
    """返回 JavaScript 执行结果的不含视觉装饰文本。"""
    return _javascript_result_text(view)


def render_tool_start_transcript_view(view: ToolStartView) -> StyledBlock:
    """把工具启动信息转换为完整记录块。"""
    spec = tool_display_spec(view.name)

    if spec.kind is ToolDisplayKind.SHELL:
        command = _command_text(view.arguments.get("command"))
        return _transcript_block(view.title, f"$ {command}" if command else "")
    if spec.kind is ToolDisplayKind.STDIN:
        return _transcript_block("", "")
    if spec.kind is ToolDisplayKind.JAVASCRIPT:
        source_field = spec.source_field
        source       = view.arguments.get(source_field) if source_field else ""

        return _transcript_block(view.title, str(source or ""))

    return _transcript_block(view.title, _json_text(view.arguments))


def render_generic_tool_result_transcript_view(view: GenericToolResultView) -> StyledBlock:
    """把普通工具结果转换为完整记录块。"""
    return _transcript_block(view.title, view.text)


def render_native_tool_result_transcript_view(view: NativeToolResultView) -> tuple[StyledBlock, ...]:
    """把原生编码工具结果转换为完整记录块。"""
    spec = tool_display_spec(view.name)
    if spec.kind is ToolDisplayKind.JAVASCRIPT:
        return (render_javascript_result_transcript_view(view),)

    payload = _native_payload(view.data)
    title   = view.entries[0].title if view.entries else f"• Ran {view.name}"

    if spec.kind is ToolDisplayKind.SHELL:
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

    if spec.kind is ToolDisplayKind.STDIN:
        stdin = str(view.arguments.get("stdin") or "")
        return (_transcript_block(title, _interaction_input_body(stdin)),)

    return (_transcript_block(title, _json_text(payload or view.data)),)


def render_tool_start_raw_text(view: ToolStartView) -> str:
    """把工具启动信息转换为无装饰文本。"""
    spec = tool_display_spec(view.name)

    if spec.kind is ToolDisplayKind.SHELL:
        return _command_text(view.arguments.get("command"))
    if spec.kind is ToolDisplayKind.JAVASCRIPT:
        source_field = spec.source_field
        return str(view.arguments.get(source_field) or "") if source_field else ""
    if spec.kind is ToolDisplayKind.STDIN:
        return ""

    return _json_text(view.arguments)


def render_generic_tool_result_raw_text(view: GenericToolResultView) -> str:
    """把普通工具结果转换为无装饰文本。"""
    return str(view.text or "")


def render_native_tool_result_raw_text(view: NativeToolResultView) -> tuple[str, ...]:
    """把原生工具结果转换为无装饰文本块。"""
    spec = tool_display_spec(view.name)

    if spec.kind is ToolDisplayKind.JAVASCRIPT:
        return (render_javascript_result_raw_text(view),)

    payload = _native_payload(view.data)

    if spec.kind is ToolDisplayKind.SHELL:
        command = _command_text(
            payload.get("command") or view.arguments.get("command")
        )
        output = _native_output_text(payload)
        return ("\n".join(item for item in (command, output) if item),)

    if spec.kind is ToolDisplayKind.STDIN:
        stdin   = str(view.arguments.get("stdin") or "")
        command = _command_text(
            payload.get("command") or view.arguments.get("command")
        )
        action = (
            "Interacted with background terminal"
            if stdin or _terminal_control(view, payload) != "none"
            else "Waited for background terminal"
        )
        heading = f"{action}: {command}" if command else action
        input_text = _interaction_input_text(stdin)
        return ("\n".join(item for item in (heading, input_text) if item),)

    return (_json_text(payload or view.data),)


def _transcript_block(title: str, body: str) -> StyledBlock:
    """生成不截断正文内容的记录块。"""
    heading = str(title or "").rstrip()
    content = str(body or "").strip("\n")
    text    = f"{heading}\n{content}" if heading and content else heading or content

    return StyledBlock(plain_text=text)


def _interaction_input_body(stdin: str) -> str:
    """生成后台终端交互记录中的输入正文。"""
    lines = _interaction_input_text(stdin).split("\n")
    if not any(line for line in lines):
        return ""
    return "\n".join(
        f"  └ {line}" if index == 0 else f"    {line}"
        for index, line in enumerate(lines)
    ).rstrip()


def _interaction_input_text(stdin: str) -> str:
    """规范化终端输入并移除仅由发送换行产生的尾部空行。"""
    lines = str(stdin or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def _terminal_control(
    view: NativeToolResultView,
    payload: dict[str, typing.Any],
) -> str:
    """返回后台终端交互使用的控制动作。"""
    return str(
        view.arguments.get("control")
        or payload.get("control")
        or "none"
    ).strip().lower()


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
            data = item.get("data") if isinstance(item, dict) else None
            if isinstance(data, dict):
                return _string_keyed_payload(data)

    return _string_keyed_payload(value)


def _string_keyed_payload(
    value: typing.Mapping[typing.Any, typing.Any],
) -> dict[str, typing.Any]:
    """复制结构化载荷并校验其字段名为字符串。"""
    normalized: dict[str, typing.Any] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise TypeError("native tool payload fields must use string keys")
        normalized[key] = item
    return normalized


def _native_output_text(payload: dict[str, typing.Any]) -> str:
    """按接收顺序返回原生命令已经保留的完整输出。"""
    output_lines = payload.get("output_lines")
    if isinstance(output_lines, (list, tuple)) and output_lines:
        return "\n".join(str(item or "").rstrip("\n") for item in output_lines)

    output = payload.get("output")
    if isinstance(output, str):
        return output
    if output is not None:
        return _json_text(output)

    stdout = str(payload.get("stdout") or "")
    stderr = str(payload.get("stderr") or "")

    return f"{stdout}{stderr}"


def _native_result_title(
    view: NativeToolResultView,
    fallback: str,
    *,
    terminal_width: int | None,
    measure_width: typing.Callable[[str], int] | None
) -> str:
    """返回按当前终端宽度生成的原生工具结果标题。"""
    if tool_display_spec(view.name).kind not in {
        ToolDisplayKind.SHELL,
        ToolDisplayKind.STDIN,
    }:
        return fallback
    title = render_tool_trace(
        view.name,
        view.arguments,
        ok=view.ok,
        data=view.data,
        cost_ms=view.cost_ms,
        terminal_width=terminal_width,
        measure_width=measure_width,
    )
    return _display_title(
        title,
        terminal_width=terminal_width,
        measure_width=measure_width,
    )


def _javascript_result_text(view: NativeToolResultView) -> str:
    """返回 JavaScript 执行保留的完整输出。"""
    payload = _native_payload(view.data)
    value   = payload.get("output") if view.ok else payload.get("error")

    if value not in (None, ""):
        if isinstance(value, str):
            return value.strip("\n")
        return _json_text(value)

    return "JavaScript cell completed." if view.ok else "JavaScript cell failed."


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

    indented_preview = preview.full.replace("\n", "\n    ")
    return f"{title}\n  └ {indented_preview}"


def _coding_trace_text(title: str, preview: TracePreview) -> str:
    """生成原生编码工具结果的记录文本。"""
    if not preview.full:
        return title

    indented_preview = preview.full.replace("\n", "\n    ")
    return f"{title}\n  └ {indented_preview}"


def _display_title(
    title: str,
    *,
    terminal_width: int | None,
    measure_width: typing.Callable[[str], int] | None,
) -> str:
    """返回适合当前终端宽度的单行工具标题。"""
    width_of = measure_width or text_display_width
    text = sanitize_terminal_line(title, measure_width=width_of)
    if not isinstance(terminal_width, int) or terminal_width <= 0:
        return text
    return clip_display_text(
        text,
        width=terminal_width,
        measure_width=width_of,
    )


if __name__ == '__main__':
    pass
