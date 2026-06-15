# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import typing
from pathlib import Path
from mind_app.stream_events.command_preview import command_text
from .common import (
    MAX_PREVIEW_WIDTH,
    MISSING,
    TraceEntry,
    TracePreview,
    _normalize_preview_lines,
    _plain_trace_preview_from_lines,
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
from .shell_batch import (
    shell_batch_trace_title,
    shell_batch_tree_preview
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
    "shell_command",
    "workspace_write_file",
    "workspace_apply_unified_patch"
}

SMALL_SHELL_BATCH_TRACE_LIMIT = 2


def render_tool_start_trace(
    name: str,
    arguments: dict[str, typing.Any]
) -> str:
    """生成工具开始执行时的轨迹标题。"""
    _ = arguments
    return f"• Tool {str(name or 'tool').strip() or 'tool'}"


def render_tool_start_preview(
    arguments: dict[str, typing.Any]
) -> TracePreview:
    """生成工具开始执行时的参数预览。"""
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
    """检查参数路径是否存在；无法检查时返回 MISSING。"""
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
    """把参数值转换为单行预览文本。"""
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
    """判断工具是否使用原生轨迹样式。"""
    return name in NATIVE_CODING_TRACE_TOOLS


def render_tool_result_preview(
    name: str,
    data: typing.Any = None,
    *,
    arguments: dict[str, typing.Any] | None = None
) -> TracePreview:
    """根据工具结果和参数生成结果预览。"""
    data = _result_payload(data)
    args = arguments if isinstance(arguments, dict) else {}

    if not data:
        return TracePreview()

    is_error = data.get("ok") is False

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
        results = data.get("results")
        if isinstance(results, list):
            inner = _single_shell_batch_payload(results)
            if inner is None:
                return shell_batch_tree_preview(data)
            inner_args = inner.get("args") if isinstance(inner.get("args"), dict) else args
            inner_data = inner.get("data") if isinstance(inner.get("data"), dict) else {}
            return render_tool_result_preview("shell_command", inner_data, arguments=inner_args)

        if is_error:
            lines = _shell_command_error_context_lines(data)
        else:
            stdout_source = data.get("stdout")
            lines         = _normalize_preview_lines(stdout_source)
            err_lines     = _normalize_preview_lines(data.get("stderr"))

            if lines and err_lines:
                lines.extend(err_lines)
            elif err_lines:
                lines = err_lines

        if not lines:
            lines = _shell_command_empty_preview_lines(data, is_error=is_error)

        return _trace_preview_from_lines(lines) if is_error else _plain_trace_preview_from_lines(lines)

    return TracePreview()


def render_tool_result_entries(
    name: str,
    arguments: dict[str, typing.Any],
    *,
    ok: bool,
    data: typing.Any = None,
    cost_ms: int | None = None,
    before_exists: typing.Any = MISSING
) -> list[TraceEntry]:
    """生成工具结果可独立展示的轨迹列表。"""
    args    = arguments if isinstance(arguments, dict) else {}
    payload = _result_payload(data)

    if name == "shell_command":
        results = payload.get("results")
        if isinstance(results, list):
            items = [item for item in results if isinstance(item, dict)]
            if 0 < len(items) <= SMALL_SHELL_BATCH_TRACE_LIMIT:
                entries: list[TraceEntry] = []

                for item in items:
                    inner = _single_shell_batch_payload([item])
                    if inner is None:
                        break

                    inner_args = inner.get("args") if isinstance(inner.get("args"), dict) else {}
                    inner_data = inner.get("data") if isinstance(inner.get("data"), dict) else {}
                    item_ok    = bool(item.get("ok")) if "ok" in item else bool(inner_data.get("ok"))

                    entries.append(TraceEntry(
                        title=render_tool_trace(
                            "shell_command",
                            inner_args,
                            ok=item_ok,
                            data=inner_data,
                            before_exists=before_exists
                        ),
                        preview=render_tool_result_preview(
                            "shell_command",
                            inner_data,
                            arguments=inner_args
                        ),
                        ok=item_ok
                    ))

                if len(entries) == len(items):
                    return entries

    return [TraceEntry(
        title=render_tool_trace(
            name,
            args,
            ok=ok,
            data=data,
            cost_ms=cost_ms,
            before_exists=before_exists
        ),
        preview=render_tool_result_preview(name, data, arguments=args),
        ok=ok
    )]


def _shell_command_error_context_lines(
    data: dict[str, typing.Any],
    *,
    max_context_lines: int = 6
) -> list[str]:
    """生成 shell_command 失败输出的上下文行。"""
    stderr_lines = _normalize_preview_lines(data.get("stderr"))
    stdout_lines = _normalize_preview_lines(data.get("stdout"))
    stream_lines = stderr_lines or stdout_lines

    if not stream_lines:
        return _summary_lines(
            ("error", data.get("error")),
        )

    diagnostic = _shell_error_diagnostic_lines(
        stream_lines,
        max_context_lines=max_context_lines
    )
    if diagnostic:
        return diagnostic

    if len(stream_lines) > max_context_lines:
        omitted = len(stream_lines) - max_context_lines
        return [
            f"… +{omitted} lines (ctrl + t to view transcript)",
            *stream_lines[-max_context_lines:]
        ]

    return stream_lines


def _shell_command_empty_preview_lines(
    data: dict[str, typing.Any],
    *,
    is_error: bool
) -> list[str]:
    """生成 shell_command 无输出时的预览；失败时避免伪造 Line 块。"""
    if not is_error:
        return ["(no output)"]

    exit_code = data.get("exit_code")
    if isinstance(exit_code, int):
        summary = f"Command failed with exit code {exit_code}"
    else:
        summary = "Command failed"

    details = []

    runtime = data.get("runtime")
    if isinstance(runtime, dict):
        name = str(runtime.get("name") or "").strip()
        prefix = runtime.get("prefix")
        if isinstance(prefix, list):
            shell = " ".join(str(item) for item in prefix if str(item or "").strip())
        else:
            shell = str(runtime.get("executable") or "").strip()
        if name or shell:
            details.append(f"runtime: {name or shell}")

    details.append("stderr empty")
    if details:
        summary = f"{summary} ({', '.join(details)})"
    return [summary]


def _shell_error_diagnostic_lines(
    lines: list[str],
    *,
    max_context_lines: int
) -> list[str]:
    """优先提取跨 shell 的结构化错误诊断块。"""
    extractors = (
        _powershell_line_diagnostic_block,
        _powershell_at_line_diagnostic_block,
        _python_traceback_diagnostic_block,
        _posix_shell_diagnostic_block,
        _tool_error_diagnostic_block,
    )

    for extractor in extractors:
        block = extractor(lines)
        if block:
            return _clip_diagnostic_block(block, max_lines=max_context_lines)

    return []


def _powershell_line_diagnostic_block(lines: list[str]) -> list[str]:
    """提取 PowerShell 的 Header/Line 管道错误块。"""
    for index, line in enumerate(lines):
        if str(line or "").strip() != "Line |":
            continue

        start = index
        if index > 0 and _is_error_header_line(lines[index - 1]):
            start = index - 1

        end = index + 1
        while end < len(lines) and _is_powershell_line_detail(lines[end]):
            end += 1

        if end > index + 1:
            return lines[start:end]

    return []


def _powershell_at_line_diagnostic_block(lines: list[str]) -> list[str]:
    """提取 Windows PowerShell 的 At line:char 错误块。"""
    for index, line in enumerate(lines):
        if not re.match(r"^At line:\d+ char:\d+", str(line or "").strip(), re.IGNORECASE):
            continue

        end = index + 1
        while end < len(lines):
            current = str(lines[end] or "")
            stripped = current.strip()
            if not stripped:
                break
            if end > index + 1 and re.match(r"^[A-Za-z][A-Za-z0-9_. -]*:$", stripped):
                break
            end += 1

        return lines[index:end]

    return []


def _python_traceback_diagnostic_block(lines: list[str]) -> list[str]:
    """提取 Python traceback，避免长堆栈只保留尾部。"""
    for index, line in enumerate(lines):
        if str(line or "").strip() == "Traceback (most recent call last):":
            return lines[index:]

    return []


def _posix_shell_diagnostic_block(lines: list[str]) -> list[str]:
    """提取 bash/zsh/sh 等 POSIX shell 常见语法错误。"""
    for index, line in enumerate(lines):
        stripped = str(line or "").strip()
        if re.match(r"^(bash|zsh|sh|dash|fish|ksh)(:|\b).*(syntax error|parse error|not found|permission denied)", stripped, re.IGNORECASE):
            return lines[index:min(len(lines), index + 3)]

    return []


def _tool_error_diagnostic_block(lines: list[str]) -> list[str]:
    """提取常见 CLI 工具错误块，如 rg regex parse error。"""
    for index, line in enumerate(lines):
        stripped = str(line or "").strip()
        if not re.match(r"^[A-Za-z0-9_.-]+: .*(error|failed|cannot|missing)", stripped, re.IGNORECASE):
            continue

        end = index + 1
        while end < len(lines):
            current = str(lines[end] or "").strip()
            if not current:
                break
            if end > index + 1 and re.match(r"^[A-Za-z0-9_.-]+: ", current) and not current.lower().startswith("error:"):
                break
            end += 1

        return lines[index:end]

    return []


def _clip_diagnostic_block(block: list[str], *, max_lines: int) -> list[str]:
    """按诊断块语义裁剪，保留头部和最终错误信息。"""
    limit = max(1, int(max_lines or 1))
    if len(block) <= limit:
        return block
    if limit <= 2:
        return block[:limit]

    head_count = max(1, limit // 2)
    tail_count = max(1, limit - head_count - 1)
    omitted = len(block) - head_count - tail_count

    return [
        *block[:head_count],
        f"… +{omitted} lines",
        *block[-tail_count:],
    ]


def _is_error_header_line(line: str) -> bool:
    """判断是否是错误块标题行。"""
    return bool(re.match(r"^[A-Za-z][A-Za-z0-9_. -]*:$", str(line or "").strip()))


def _is_powershell_line_detail(line: str) -> bool:
    """判断是否是 PowerShell Line | 块的详情行。"""
    text = str(line or "")
    stripped = text.strip()
    return bool(
        re.match(r"^\d+\s+\|", stripped)
        or re.match(r"^\|", stripped)
        or re.match(r"^[~^]+$", stripped)
    )


def _shell_command_title(command: typing.Any) -> str:
    """生成 shell_command 标题中的命令摘要。"""
    lines = _shell_command_raw_lines(command)
    if not lines:
        return "shell command"
    return _short_text(lines[0], MAX_PREVIEW_WIDTH)


def _shell_command_raw_lines(command: typing.Any) -> list[str]:
    """按原始换行拆分命令；数组命令保持参数间空格。"""
    text = command_text(command) if isinstance(command, list) else str(command or "").strip()

    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while lines and not lines[-1]:
        lines.pop()

    return lines


def render_tool_trace(
    name: str,
    arguments: dict[str, typing.Any],
    *,
    ok: bool,
    data: typing.Any = None,
    cost_ms: int | None = None,
    before_exists: typing.Any = MISSING
) -> str:
    """生成工具执行完成后的轨迹标题。"""
    _ = cost_ms

    args    = arguments if isinstance(arguments, dict) else {}
    payload = _result_payload(data)

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
        results = payload.get("results")
        if isinstance(results, list):
            inner = _single_shell_batch_payload(results)
            if inner is None:
                return shell_batch_trace_title(payload, cost_ms=cost_ms)
            inner_args = inner.get("args") if isinstance(inner.get("args"), dict) else args
            inner_data = inner.get("data") if isinstance(inner.get("data"), dict) else {}
            return render_tool_trace(
                "shell_command",
                inner_args,
                ok=ok,
                data=inner_data,
                cost_ms=cost_ms,
                before_exists=before_exists
            )

        command = _shell_command_title(payload.get("command") or args.get("command"))
        return f"• Ran {command}".rstrip()

    summary = _short_text(args, 100)
    detail  = f" {summary}" if summary else ""

    return f"• Ran {name}{detail}"


def _single_shell_batch_payload(
    results: list[typing.Any]
) -> dict[str, typing.Any] | None:
    """从单条 shell_command 批量结果中提取单命令参数和数据。"""
    items = [item for item in results if isinstance(item, dict)]
    if len(items) != 1:
        return None

    item   = items[0]
    result = item.get("result") if isinstance(item.get("result"), dict) else {}
    data   = _result_payload(result)

    return {
        "args" : item.get("args") if isinstance(item.get("args"), dict) else {},
        "data" : data
    }


if __name__ == '__main__':
    pass
