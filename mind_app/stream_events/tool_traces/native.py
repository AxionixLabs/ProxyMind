# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from pathlib import Path
from mind_app.stream_events.command_preview import (
    command_preview,
    inline_script_preview_lines
)
from .common import (
    MAX_PREVIEW_WIDTH,
    MISSING,
    TracePreview,
    _normalize_preview_lines,
    _result_payload,
    _short_text,
    _summary_lines,
    _trace_code_preview_from_lines,
    _trace_preview_from_lines
)
from .native_helpers import (
    _error_preview_lines,
    _file_action_from_before_exists,
    _format_delta,
    _format_size,
    _path_from_args,
    _short_sha,
    _unified_file_action
)
from .shell_calls import (
    shell_calls_trace_title,
    shell_calls_tree_preview
)
from .native_patch import (
    _error_unified_patch_preview_lines,
    _hunk_label,
    _line_delta_from_content,
    _line_delta_from_unified_files,
    _numbered_added_lines,
    _patch_error_diagnostic_lines,
    _unified_patch_preview_lines
)

NATIVE_CODING_TRACE_TOOLS = {
    "shell_calls",
    "workspace_write_file",
    "workspace_apply_unified_patch",
    "shell_command"
}


def render_tool_start_trace(
    name: str,
    arguments: dict[str, typing.Any]
) -> str:
    """渲染普通工具开始执行前的轨迹行。"""
    _ = arguments
    return f"• Tool {str(name or 'tool').strip() or 'tool'}"


def render_tool_start_preview(
    arguments: dict[str, typing.Any]
) -> TracePreview:
    """生成普通工具开始执行前的参数摘要。"""
    if not isinstance(arguments, dict) or not arguments:
        return _trace_preview_from_lines(["no args"])

    lines: list[str] = []
    for key in sorted(arguments, key=lambda item: str(item))[:6]:
        value = arguments.get(key)
        lines.append(f"{key}={_argument_preview(value)}")
    if len(arguments) > 6:
        lines.append(f"… +{len(arguments) - 6} args")

    return _trace_preview_from_lines(lines)


def local_path_exists(
    arguments: dict[str, typing.Any]
) -> typing.Any:
    """判断参数中的路径是否存在；无法判断时返回 MISSING。"""
    if not isinstance(arguments, dict):
        return MISSING
    raw_path = str(arguments.get("path") or "").strip()
    if not raw_path:
        return MISSING
    try:
        return Path(raw_path).expanduser().resolve().exists()
    except OSError:
        return MISSING


def _argument_preview(value: typing.Any) -> str:
    if isinstance(value, str):
        text = _short_text(value, MAX_PREVIEW_WIDTH)
        return repr(text)
    if isinstance(value, (int, float, bool)) or value is None:
        return str(value)
    if isinstance(value, dict):
        return f"<dict:{len(value)}>"
    if isinstance(value, (list, tuple, set)):
        return f"<{type(value).__name__}:{len(value)}>"
    return _short_text(value, MAX_PREVIEW_WIDTH)


def is_native_coding_trace_tool(
    name: str
) -> bool:
    """判断工具是否使用原生编码轨迹样式。"""
    return name in NATIVE_CODING_TRACE_TOOLS


def render_tool_result_preview(
    name: str,
    data: typing.Any = None,
    *,
    arguments: dict[str, typing.Any] | None = None
) -> TracePreview:
    """根据工具结果和参数生成执行结果预览。"""
    data = _result_payload(data)
    args = arguments if isinstance(arguments, dict) else {}

    if not data:
        return TracePreview()

    is_error = data.get("ok") is False

    if name == "shell_calls":
        return shell_calls_tree_preview(data)

    if name == "workspace_write_file":
        if is_error:
            return _trace_preview_from_lines(_error_preview_lines(
                data,
                ("path", data.get("path") or args.get("path")),
            ))
        content = args.get("content")
        if content is not None:
            return _trace_code_preview_from_lines(
                _numbered_added_lines(content)
            )
        return _trace_preview_from_lines(_summary_lines(
            ("file", str(data.get("path") or "").strip()),
            ("size", _format_size(data.get("bytes"))),
            ("sha256", _short_sha(data.get("sha256"))),
        ))

    if name == "workspace_apply_unified_patch":
        if is_error:
            prefix = _patch_error_diagnostic_lines(data)

            preview_lines = _error_unified_patch_preview_lines(args.get("patch"), data)
            if preview_lines:
                return _trace_code_preview_from_lines([*prefix, *preview_lines])
            return _trace_preview_from_lines(prefix)

        preview_lines = _unified_patch_preview_lines(args.get("patch"))
        if preview_lines:
            return _trace_code_preview_from_lines(preview_lines)

        files = data.get("files")
        if isinstance(files, list):
            lines = []
            for item in files:
                if not isinstance(item, dict):
                    continue

                action    = str(item.get("action") or "modify").strip() or "modify"
                path      = str(item.get("path") or "").strip()
                hunk_text = _hunk_label(item.get("hunks"))
                sha       = _short_sha(item.get("sha256"))
                line      = f"{action} {path}".strip()
                details   = []

                if hunk_text:
                    details.append(hunk_text)

                added   = item.get("added_lines")
                removed = item.get("removed_lines")

                if isinstance(added, int) or isinstance(removed, int):
                    delta = _format_delta(int(added or 0), int(removed or 0)).strip()
                    if delta:
                        details.append(delta)
                if sha:
                    details.append(f"sha256={sha}")
                if details:
                    line = f"{line} ({', '.join(details)})"
                if line:
                    lines.append(line)

            return _trace_preview_from_lines(lines)

    if name == "shell_command":

        preview = command_preview(data.get("command") or args.get("command"))

        if is_error:
            lines = _shell_command_error_context_lines(data, command=preview.title)
        else:
            stdout_source = data.get("stdout")
            lines         = _normalize_preview_lines(stdout_source)
            err_lines     = _normalize_preview_lines(data.get("stderr"))

            if lines and err_lines:
                lines.extend(err_lines)
            elif err_lines:
                lines = err_lines

        has_inline_script = False
        if preview.has_script:
            script_lines = inline_script_preview_lines(preview.script, path=preview.path)
            if script_lines:
                has_inline_script = True
                lines = [*script_lines, *lines]

        if not lines:
            lines = ["(no output)"]

        if has_inline_script:
            return _trace_code_preview_from_lines(lines)
        return _trace_preview_from_lines(lines)

    return TracePreview()


def _shell_command_error_context_lines(
    data: dict[str, typing.Any],
    *,
    command: str = "",
    max_context_lines: int = 6
) -> list[str]:
    """把 shell_command 异常预览压缩成尾部输出上下文。"""
    stderr_lines = _normalize_preview_lines(data.get("stderr"))
    stdout_lines = _normalize_preview_lines(data.get("stdout"))
    stream_lines = stderr_lines or stdout_lines
    if not stream_lines:
        return _summary_lines(
            ("error", data.get("error")),
        )

    split_single = _split_single_line_shell_error(stream_lines, command=command)
    if split_single:
        return split_single

    if len(stream_lines) > max_context_lines:
        omitted = len(stream_lines) - max_context_lines
        return [
            f"… +{omitted} lines (ctrl + t to view transcript)",
            *stream_lines[-max_context_lines:]
        ]

    return stream_lines


def _split_single_line_shell_error(
    lines: list[str],
    *,
    command: str,
    line_number: int = 1
) -> list[str]:
    """把单行命令错误整理成通用定位预览。"""
    if len(lines) != 1:
        return []

    line = str(lines[0] or "").strip()
    if not line:
        return []

    head, sep, message = line.partition(": ")
    if not sep or not head or not message:
        head = "Command"
        message = line

    command = str(command or "").strip()
    if not command:
        return []

    line_number = max(1, int(line_number or 1))
    command_line = f"{line_number:4d} |  {command}"
    marker_width = max(8, min(MAX_PREVIEW_WIDTH - 8, len(command)))
    return [
        f"{head}:",
        "Line |",
        command_line,
        f"     |  {'~' * marker_width}",
        f"     | {message}",
    ]


def render_tool_trace(
    name: str,
    arguments: dict[str, typing.Any],
    *,
    ok: bool,
    data: typing.Any = None,
    cost_ms: int | None = None,
    before_exists: typing.Any = MISSING
) -> str:
    """渲染工具完成后的轨迹摘要行。"""
    _ = cost_ms

    args    = arguments if isinstance(arguments, dict) else {}
    payload = _result_payload(data)

    if name == "shell_calls":
        return shell_calls_trace_title(payload, cost_ms=cost_ms)

    if name == "workspace_write_file":
        path = str(payload.get("path") or _path_from_args(args))
        added, removed = _line_delta_from_content(args.get("content"))
        action = _file_action_from_before_exists(before_exists)

        return f"• {action} {path}{_format_delta(added, removed)}"

    if name == "workspace_apply_unified_patch":
        if not ok:
            return "• Patch"

        files  = payload.get("files")
        action = _unified_file_action(files)

        if isinstance(files, list) and len(files) == 1 and isinstance(files[0], dict):
            target = str(files[0].get("path") or "patch")
        elif isinstance(files, list):
            target = f"{len(files)} files"
        else:
            target = "patch"

        added, removed = _line_delta_from_unified_files(payload)
        return f"• {action} {target}{_format_delta(added, removed)}"

    if name == "shell_command":
        command = command_preview(payload.get("command") or args.get("command")).title
        return f"• Ran {command}".rstrip()

    summary = _short_text(args, 100)
    detail  = f" {summary}" if summary else ""

    return f"• Ran {name}{detail}"


if __name__ == '__main__':
    pass
