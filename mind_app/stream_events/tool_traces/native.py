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
    _failure_preview_lines,
    _failure_suffix,
    _file_action_from_args,
    _format_delta,
    _format_size,
    _list_file_preview_lines,
    _path_from_args,
    _search_query_label,
    _short_sha,
    _unified_action
)
from .native_parallel import (
    _parallel_read_failure_summary,
    _parallel_read_item_payload,
    _parallel_read_item_target,
    _parallel_read_reason_label
)
from .native_patch import (
    _failed_unified_patch_preview_lines,
    _hunk_label,
    _line_delta_from_content,
    _line_delta_from_patch_args,
    _line_delta_from_unified_files,
    _numbered_added_lines,
    _patch_failure_diagnostic_lines,
    _patch_replacement_preview,
    _unified_patch_preview_lines
)

NATIVE_CODING_TRACE_TOOLS = {
    "workspace_root",
    "workspace_list_file",
    "workspace_read_file",
    "workspace_search",
    "native_parallel_read",
    "workspace_write_file",
    "workspace_copy_file",
    "workspace_move_file",
    "workspace_delete_file",
    "workspace_apply_patch",
    "workspace_apply_unified_patch",
    "shell_exec",
    "git_status",
    "git_diff",
    "change_summary"
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
    failed = data.get("ok") is False

    if name == "workspace_root":
        if failed:
            return _trace_preview_from_lines(_failure_preview_lines(data))
        root = str(data.get("root") or "").strip()
        return _trace_preview_from_lines([f"root={root}"] if root else [])

    if name == "workspace_list_file":
        if failed:
            return _trace_preview_from_lines(_failure_preview_lines(
                data,
                ("path", data.get("path") or args.get("path")),
            ))
        files = data.get("files")
        if isinstance(files, list):
            return _trace_preview_from_lines(_list_file_preview_lines(files))

    if name == "workspace_read_file":
        if failed:
            return _trace_preview_from_lines(_failure_preview_lines(
                data,
                ("path", data.get("path") or args.get("path")),
            ))
        return _trace_preview_from_lines(_normalize_preview_lines(data.get("content")))

    if name == "workspace_search":
        if failed:
            return _trace_preview_from_lines(_failure_preview_lines(
                data,
                ("query", args.get("query")),
            ))
        matches = data.get("matches")
        if isinstance(matches, list):
            lines = []
            for item in matches:
                if isinstance(item, dict):

                    kind = str(item.get("kind") or "").strip()
                    path = str(item.get("path") or "")
                    line = str(item.get("line") or "")
                    text = _short_text(
                        item.get("text") or item.get("signature") or item.get("qualified_name") or item.get("name"),
                        MAX_PREVIEW_WIDTH,
                    )
                    loc  = f"{path}:{line}" if line else path
                    row  = loc if kind == "file" and not line else f"{loc} {text}".strip()

                    if row:
                        lines.append(row)

            return _trace_preview_from_lines(lines)

    if name == "native_parallel_read":
        results = data.get("results")
        if isinstance(results, list):
            lines = []
            for item in results:
                if not isinstance(item, dict):
                    continue

                index   = item.get("index")
                tool    = str(item.get("tool") or "").strip()
                payload = _parallel_read_item_payload(item)
                target  = _parallel_read_item_target(item, payload)
                state   = "ok" if item.get("ok") else _parallel_read_reason_label(payload.get("reason"))

                label = "read" if tool == "workspace_read_file" else (
                    "list" if tool == "workspace_list_file" else (
                        "search" if tool == "workspace_search" else (
                            "root" if tool == "workspace_root" else tool or "item"
                        )
                    )
                )
                prefix = f"{index}: " if index is not None else ""
                detail = f" {target}" if target else ""
                lines.append(f"{prefix}{state} {label}{detail}".strip())

            return _trace_preview_from_lines(lines)

    if name == "workspace_write_file":
        if failed:
            return _trace_preview_from_lines(_failure_preview_lines(
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

    if name == "workspace_copy_file":
        if failed:
            return _trace_preview_from_lines(_failure_preview_lines(
                data,
                ("from", data.get("source_path") or args.get("source_path")),
                ("to", data.get("target_path") or args.get("target_path")),
            ))

        source = str(data.get("source_path") or "").strip()
        target = str(data.get("target_path") or "").strip()
        size   = _format_size(data.get("bytes"))
        sha    = _short_sha(data.get("sha256"))

        return _trace_preview_from_lines(_summary_lines(
            ("from", source),
            ("to", target),
            ("size", size),
            ("sha256", sha)
        ))

    if name == "workspace_move_file":
        if failed:
            return _trace_preview_from_lines(_failure_preview_lines(
                data,
                ("from", data.get("source_path") or args.get("source_path")),
                ("to", data.get("target_path") or args.get("target_path")),
            ))
        source = str(data.get("source_path") or "").strip()
        target = str(data.get("target_path") or "").strip()
        size   = _format_size(data.get("bytes"))
        sha    = _short_sha(data.get("sha256"))

        return _trace_preview_from_lines(_summary_lines(
            ("from", source),
            ("to", target),
            ("size", size),
            ("sha256", sha)
        ))

    if name == "workspace_delete_file":
        if failed:
            return _trace_preview_from_lines(_failure_preview_lines(
                data,
                ("path", data.get("path") or args.get("path")),
            ))
        path = str(data.get("path") or "").strip()
        size = _format_size(data.get("bytes"))
        sha  = _short_sha(data.get("sha256"))

        return _trace_preview_from_lines(_summary_lines(
            ("file", path),
            ("removed", size),
            ("sha256", sha)
        ))

    if name == "workspace_apply_patch":
        if failed:
            return _trace_preview_from_lines(_failure_preview_lines(
                data,
                ("path", data.get("path") or args.get("path")),
                ("found", data.get("found")),
                ("expected", data.get("expected")),
            ))
        preview_lines = _patch_replacement_preview(args)
        if preview_lines:
            return _trace_code_preview_from_lines(
                preview_lines
            )
        return _trace_preview_from_lines(_summary_lines(
            ("file", str(data.get("path") or "").strip()),
            ("replacements", data.get("replacements")),
            ("sha256", _short_sha(data.get("sha256"))),
        ))

    if name == "workspace_apply_unified_patch":
        if failed:
            preview_lines = _failed_unified_patch_preview_lines(args.get("patch"), data)
            prefix = _patch_failure_diagnostic_lines(data)
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

    if name in {"shell_exec", "git_status", "git_diff"}:

        stdout_source = data.get("stdout")
        lines         = _normalize_preview_lines(stdout_source)
        err_lines     = _normalize_preview_lines(data.get("stderr"))

        prefix = []

        if failed:
            prefix = _failure_preview_lines(
                data,
                ("exit_code", data.get("exit_code")),
            )

        if lines and err_lines:
            lines.extend(err_lines)
        elif err_lines:
            lines = err_lines
        if not lines and name == "shell_exec" and data.get("exit_code") is not None:
            lines = [f"exit_code={data.get('exit_code')}"]
        if not lines and name == "git_status" and data.get("ok") is True:
            lines = ["No changes in git status"]
        if not lines and name == "git_diff" and data.get("ok") is True:
            lines = ["No tracked changes in git diff"]

        has_inline_script = False
        if name == "shell_exec":
            preview = command_preview(data.get("command") or args.get("command"))
            if preview.has_script:
                script_lines = inline_script_preview_lines(preview.script, path=preview.path)
                if script_lines:
                    has_inline_script = True
                    lines = [*script_lines, *lines]

        if prefix:
            lines = [*prefix, *lines]

        if name == "shell_exec" and has_inline_script:
            return _trace_code_preview_from_lines(lines)
        return _trace_preview_from_lines(lines)

    if name == "change_summary":
        verification = data.get("verification") if isinstance(data.get("verification"), dict) else {}
        blockers     = data.get("blockers") if isinstance(data.get("blockers"), list) else []
        warnings     = data.get("warnings") if isinstance(data.get("warnings"), list) else []
        diff_stats   = data.get("diff_stats") if isinstance(data.get("diff_stats"), dict) else {}

        lines = _summary_lines(
            ("ready", data.get("ready")),
            ("verification", verification.get("reason")),
            ("files", data.get("file_count")),
            ("diff", f"+{diff_stats.get('added_lines', 0)} -{diff_stats.get('deleted_lines', 0)}" if diff_stats else ""),
        )
        for item in blockers[:3]:
            if isinstance(item, dict):
                lines.append(f"blocker: {item.get('kind')}")
        for item in warnings[:3]:
            if isinstance(item, dict):
                lines.append(f"warning: {item.get('kind')}")
        if not lines:
            lines = _normalize_preview_lines(
                data.get("summary") or data.get("diff") or data.get("git_status")
            )
        return _trace_preview_from_lines(lines)

    return TracePreview()


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
    args    = arguments if isinstance(arguments, dict) else {}
    payload = _result_payload(data)
    suffix  = _failure_suffix(payload, ok=ok)

    if name == "workspace_root":

        root = str(payload.get("root") or "").strip()
        return f"• Root {root}".rstrip()

    if name == "workspace_list_file":

        path   = str(payload.get("path") or _path_from_args(args))
        count  = payload.get("file_count")
        detail = f" ({count} files)" if isinstance(count, int) else ""

        return f"• Listed {path}{detail}{suffix}"

    if name == "workspace_read_file":

        path = str(payload.get("path") or _path_from_args(args))
        return f"• Read {path}{suffix}"

    if name == "workspace_search":

        query, quote_query = _search_query_label(args)
        if payload.get("skipped") and payload.get("reason") == "query_empty":
            return "• Skipped empty search"

        matches = payload.get("matches")
        total   = len(matches) if isinstance(matches, list) else None
        detail  = f" ({total} matches)" if isinstance(total, int) else ""
        target  = f"\"{query}\"" if quote_query else query

        return f"• Searched {target}{detail}{suffix}"

    if name == "native_parallel_read":

        total      = payload.get("total")
        ok_count   = payload.get("ok_count")
        fail_count = payload.get("fail_count")
        detail     = ""

        if isinstance(total, int):
            detail = f" ({total} items"
            if isinstance(ok_count, int):
                detail += f", {ok_count} ok"
            if isinstance(fail_count, int) and fail_count:
                failure_summary = _parallel_read_failure_summary(payload)
                detail += f", {failure_summary or f'{fail_count} failed'}"
            detail += ")"

        return f"• Read context{detail}{suffix}"

    if name == "workspace_write_file":

        path = str(payload.get("path") or _path_from_args(args))
        added, removed = _line_delta_from_content(args.get("content"))
        action = _file_action_from_args(args, before_exists)

        return f"• {action} {path}{_format_delta(added, removed)}{suffix}"

    if name == "workspace_copy_file":

        source = str(payload.get("source_path") or args.get("source_path") or "").strip()
        target = str(payload.get("target_path") or args.get("target_path") or "").strip()
        return f"• Copied {source} -> {target}{suffix}".rstrip()

    if name == "workspace_move_file":

        source = str(payload.get("source_path") or args.get("source_path") or "").strip()
        target = str(payload.get("target_path") or args.get("target_path") or "").strip()
        return f"• Moved {source} -> {target}{suffix}".rstrip()

    if name == "workspace_delete_file":

        path = str(payload.get("path") or _path_from_args(args))
        return f"• Deleted {path}{suffix}"

    if name == "workspace_apply_patch":

        path = str(payload.get("path") or _path_from_args(args))
        added, removed = _line_delta_from_patch_args(args)
        return f"• Edited {path}{_format_delta(added, removed)}{suffix}"

    if name == "workspace_apply_unified_patch":

        if not ok:
            reason = str(payload.get("reason") or "").strip()
            detail = f": {reason}" if reason else ""
            return f"• Patch failed{detail}"
        files = payload.get("files")
        if isinstance(files, list) and len(files) == 1 and isinstance(files[0], dict):
            target = str(files[0].get("path") or "patch")
        elif isinstance(files, list):
            target = f"{len(files)} files"
        else:
            target = "patch"

        added, removed = _line_delta_from_unified_files(payload)
        action = _unified_action(files)

        return f"• {action} {target}{_format_delta(added, removed)}{suffix}"

    if name == "shell_exec":

        command = command_preview(payload.get("command") or args.get("command")).title
        rc      = payload.get("exit_code")
        elapsed = payload.get("elapsed_ms", cost_ms)
        status  = ""

        if rc is not None:
            status += f" exit_code={rc}"
        if elapsed is not None:
            status += f" elapsed_ms={elapsed}"

        return f"• Ran {command}{status}{suffix}".rstrip()

    if name == "git_status":
        return f"• Git status{suffix}"

    if name == "git_diff":
        return f"• Git diff{suffix}"

    if name == "change_summary":
        return f"• Change summary{suffix}"

    summary = _short_text(args, 100)
    detail  = f" {summary}" if summary else ""

    return f"• Ran {name}{detail}{suffix}"


if __name__ == '__main__':
    pass
