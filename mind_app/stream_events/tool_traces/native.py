# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_app.stream_events.command_preview import command_text
from .common import (
    MAX_PREVIEW_WIDTH,
    SCREEN_PREVIEW_LINES,
    TraceEntry,
    TracePreview,
    _plain_trace_preview_from_lines,
    _result_payload,
    _short_text,
    _summary_lines,
    _trace_code_preview_from_lines,
    _trace_preview_from_lines
)
from .native_helpers import (
    _format_delta,
    _short_sha,
    _patch_file_action
)
from .shell_batch import (
    shell_batch_trace_title,
    shell_batch_tree_preview
)
from .shell_errors import (
    shell_error_diagnostic_lines,
    shell_output_lines
)
from .native_patch import (
    _error_patch_preview_lines,
    _hunk_label,
    _line_delta_from_patch_files,
    _patch_error_diagnostic_lines,
    _patch_preview_lines
)

NATIVE_CODING_TRACE_TOOLS = {
    "shell_command",
    "apply_patch"
}

SMALL_SHELL_BATCH_TRACE_LIMIT = 2


def coding_trace_tool(
    name: str
) -> bool:
    """判断工具是否使用原生轨迹样式。"""
    return name in NATIVE_CODING_TRACE_TOOLS


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

    if name == "apply_patch":
        if is_error:
            prefix = _patch_error_diagnostic_lines(data)

            preview_lines = _error_patch_preview_lines(args.get("patch"), data)
            if preview_lines:
                return _trace_code_preview_from_lines([*prefix, *preview_lines])
            if not prefix:
                prefix = ["error: patch failed"]
            return _trace_preview_from_lines(prefix)

        preview_lines = _patch_preview_lines(args.get("patch"))
        if preview_lines:
            preview = _trace_code_preview_from_lines(preview_lines)
            return TracePreview(
                full=preview.full,
                screen=preview.screen,
                omitted_lines=preview.omitted_lines,
                kind="patch_tree"
            )

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
            lines         = shell_output_lines(stdout_source)
            err_lines     = shell_output_lines(data.get("stderr"))

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
    cost_ms: int | None = None
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
                            data=inner_data
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
            cost_ms=cost_ms
        ),
        preview=render_tool_result_preview(name, data, arguments=args),
        ok=ok
    )]


def render_tool_trace(
    name: str,
    arguments: dict[str, typing.Any],
    *,
    ok: bool,
    data: typing.Any = None,
    cost_ms: int | None = None
) -> str:
    """生成工具执行完成后的轨迹标题。"""
    _ = cost_ms

    args    = arguments if isinstance(arguments, dict) else {}
    payload = _result_payload(data)

    if name == "apply_patch":
        if not ok:
            return "• Patch"

        files  = payload.get("files")
        action = _patch_file_action(files)

        if isinstance(files, list) and len(files) == 1 and isinstance(files[0], dict):
            target = str(files[0].get("path") or "patch")
        elif isinstance(files, list):
            target = f"{len(files)} files"
        else:
            target = "patch"

        added, removed = _line_delta_from_patch_files(payload)
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
                cost_ms=cost_ms
            )

        command = _shell_command_title(payload.get("command") or args.get("command"))
        return f"• Ran {command}".rstrip()

    summary = _short_text(args, 100)
    detail  = f" {summary}" if summary else ""

    return f"• Ran {name}{detail}"


def _shell_command_error_context_lines(
    data: dict[str, typing.Any],
    *,
    max_context_lines: int = SCREEN_PREVIEW_LINES
) -> list[str]:
    """生成 shell_command 失败输出的上下文行。"""
    stderr_lines = shell_output_lines(data.get("stderr"))
    stdout_lines = shell_output_lines(data.get("stdout"))
    stream_lines = stderr_lines or stdout_lines

    if not stream_lines:
        return _summary_lines(
            ("error", data.get("error")),
        )

    diagnostic = shell_error_diagnostic_lines(
        stream_lines, max_context_lines=max_context_lines
    )
    if diagnostic:
        return diagnostic

    if len(stream_lines) > max_context_lines:
        tail_limit = max(1, int(max_context_lines or 1) - 1)
        tail_lines = _shell_tail_preview_lines(stream_lines, tail_limit)

        omitted = max(0, len(stream_lines) - len(tail_lines))

        return [
            f"… +{omitted} lines",
            *tail_lines
        ]

    return stream_lines


def _shell_tail_preview_lines(lines: list[str], limit: int) -> list[str]:
    """返回尾部有信息量的行，避免空行占满失败预览。"""
    wanted = max(1, int(limit or 1))

    picked: list[str] = []

    for line in reversed(lines):
        if str(line or "").strip():
            picked.append(line)
        if len(picked) >= wanted:
            break

    if picked:
        return list(reversed(picked))

    return lines[-wanted:]


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
        name   = str(runtime.get("name") or "").strip()
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
