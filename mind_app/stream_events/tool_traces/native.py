# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import typing
from pathlib import Path
from .common import (
    MAX_PREVIEW_WIDTH,
    MISSING,
    TracePreview,
    _normalize_preview_lines,
    _result_payload,
    _short_line,
    _short_text,
    _summary_lines,
    _trace_code_preview_from_lines,
    _trace_preview_from_lines
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
    "change_summary",
    "rollback_run"
}


def _path_from_args(args: dict[str, typing.Any]) -> str:
    """从工具参数中读取路径。"""
    return str(args.get("path") or ".").strip() or "."


def local_path_exists(arguments: dict[str, typing.Any]) -> typing.Any:
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


def _command_text(command: typing.Any) -> str:
    """把命令参数转换为单行文本。"""
    if isinstance(command, list):
        return " ".join(str(item) for item in command)
    return str(command or "").strip()


def _count_from_payload(payload: dict[str, typing.Any], key: str, count_key: str) -> typing.Optional[int]:
    """从结果载荷中读取列表长度或显式计数字段。"""
    items = payload.get(key)
    if isinstance(items, list):
        return len(items)
    value = payload.get(count_key)
    return value if isinstance(value, int) else None


def _session_id_from_payload(payload: dict[str, typing.Any], args: dict[str, typing.Any]) -> str:
    """从结果载荷或参数中读取会话 ID。"""
    return str(payload.get("session_id") or args.get("session_id") or "").strip()


def _status_from_payload(payload: dict[str, typing.Any]) -> str:
    """从结果载荷中读取状态文本。"""
    raw = str(payload.get("status") or "").strip().lower()
    if raw in {"success", "failed", "cancelled", "timeout"}:
        return raw
    if payload.get("ok"):
        return "success"
    if payload.get("ok") is False:
        return "failed"

    return ""


def _line_delta_from_content(content: typing.Any) -> tuple[int, int]:
    """根据完整内容估算新增和删除行数。"""
    text = str(content or "")
    if not text:
        return 0, 0
    return len(text.splitlines()) or 1, 0


def _line_delta_from_patch_args(args: dict[str, typing.Any]) -> tuple[int, int]:
    """根据文本替换参数估算新增和删除行数。"""
    old_lines    = str(args.get("old_text") or "").splitlines()
    new_lines    = str(args.get("new_text") or "").splitlines()
    replacements = max(1, int(args.get("expected_replacements") or 1))

    return (len(new_lines) or 1) * replacements, (len(old_lines) or 1) * replacements


def _line_delta_from_unified_files(data: dict[str, typing.Any]) -> tuple[int, int]:
    """从 unified patch 结果中读取新增和删除行数。"""
    added   = data.get("added_lines")
    removed = data.get("removed_lines")

    if isinstance(added, int) or isinstance(removed, int):
        return int(added or 0), int(removed or 0)

    files = data.get("files")
    if not isinstance(files, list):
        return 0, 0

    total_added   = 0
    total_removed = 0

    for item in files:
        if not isinstance(item, dict):
            continue
        total_added += int(item.get("added_lines") or 0)
        total_removed += int(item.get("removed_lines") or 0)

    return total_added, total_removed


def _format_delta(added: int, removed: int) -> str:
    """格式化新增和删除行数摘要。"""
    if added <= 0 and removed <= 0:
        return ""
    return f" (+{max(0, added)} -{max(0, removed)})"


def _failure_suffix(payload: dict[str, typing.Any], *, ok: bool) -> str:
    """生成失败标题后缀，并优先带上失败原因。"""
    if ok:
        return ""
    reason = str(payload.get("reason") or "").strip()
    return f" failed: {reason}" if reason else " failed"


def _short_sha(value: typing.Any) -> str:
    """生成短哈希文本。"""
    text = str(value or "").strip()
    return text[:12] if text else ""


def _format_size(value: typing.Any) -> str:
    """格式化字节大小。"""
    try:
        size = int(value)
    except (TypeError, ValueError):
        return ""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _diagnostic_sequence_lines(label: str, value: typing.Any) -> list[str]:
    """把失败诊断中的 expected/actual 序列压缩成单行显示。"""
    if isinstance(value, list):
        text = " | ".join(str(item) for item in value[:3])
        if len(value) > 3:
            text = f"{text} | ..."
    else:
        text = str(value or "")
    text = _short_line(text, 100)
    return [f"{label}: {text}"] if text else []


def _patch_failure_diagnostic_lines(data: dict[str, typing.Any]) -> list[str]:
    """生成 patch 失败时优先展示的诊断摘要。"""
    lines = _summary_lines(
        ("reason", data.get("reason")),
        ("file", data.get("path")),
        ("hunk", data.get("hunk_header") or data.get("header")),
        ("line", data.get("target_line") or data.get("line")),
        ("hint", data.get("patch_format_hint")),
        ("next", data.get("suggested_next_action")),
    )
    lines.extend(_diagnostic_sequence_lines("expected", data.get("expected_sequence") or data.get("expected")))
    lines.extend(_diagnostic_sequence_lines("actual", data.get("actual_sequence") or data.get("actual")))

    nearby = data.get("nearby")
    if isinstance(nearby, list) and nearby:
        sample = []
        for item in nearby[:3]:
            if not isinstance(item, dict):
                continue
            line = item.get("line")
            text = _short_line(item.get("text"), 80)
            sample.append(f"{line}: {text}" if line is not None else text)
        if sample:
            lines.append(f"nearby: {' | '.join(sample)}")

    return lines


def _numbered_added_lines(content: typing.Any, *, start_line: int = 1) -> list[str]:
    """把新增内容格式化为带行号的预览行。"""
    lines = _normalize_preview_lines(content)
    width = max(4, len(str(start_line + len(lines))))
    return [
        f"{line_no:>{width}} +{line}"
        for line_no, line in enumerate(lines, start=start_line)
    ]


def _numbered_removed_lines(content: typing.Any, *, start_line: int = 1) -> list[str]:
    """把删除内容格式化为带行号的预览行。"""
    lines = _normalize_preview_lines(content)
    width = max(4, len(str(start_line + len(lines))))
    return [
        f"{line_no:>{width}} -{line}"
        for line_no, line in enumerate(lines, start=start_line)
    ]


def _patch_replacement_preview(args: dict[str, typing.Any]) -> list[str]:
    """根据文本替换参数生成代码预览行。"""
    old_lines = _numbered_removed_lines(args.get("old_text"))
    new_lines = _numbered_added_lines(args.get("new_text"))
    if old_lines and new_lines:
        return [*old_lines, *new_lines]
    return new_lines or old_lines


def _unified_patch_preview_lines(patch: typing.Any) -> list[str]:
    """从 unified diff 文本中提取可显示的代码预览行。"""
    lines: list[str] = []
    current_new_line = 1
    for raw in _normalize_preview_lines(patch):
        if raw.startswith("--- ") or raw.startswith("+++ "):
            continue
        if raw.startswith("@@ "):
            match = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", raw)
            if match:
                current_new_line = int(match.group(1))
            lines.append(raw)
            continue
        if not raw:
            continue
        marker = raw[0]
        text = raw[1:] if marker in {" ", "+", "-"} else raw
        if marker == "+":
            lines.append(f"{current_new_line:>4} +{text}")
            current_new_line += 1
        elif marker == "-":
            lines.append(f"{current_new_line:>4} -{text}")
        elif marker == " ":
            lines.append(f"{current_new_line:>4}  {text}")
            current_new_line += 1
    return lines


def _hunk_label(value: typing.Any) -> str:
    """格式化 hunk 数量。"""
    try:
        count = int(value)
    except (TypeError, ValueError):
        return ""
    suffix = "hunk" if count == 1 else "hunks"
    return f"{count} {suffix}"


def _file_action_from_args(args: dict[str, typing.Any], before_exists: typing.Any) -> str:
    """根据参数和原路径状态判断文件动作。"""
    if before_exists is False:
        return "Added"
    if before_exists is True:
        return "Edited"
    if args.get("overwrite") is False:
        return "Added"

    return "Edited"


def _unified_action(files: typing.Any) -> str:
    """根据 unified patch 文件动作集合生成摘要动作。"""
    if not isinstance(files, list) or not files:
        return "Edited"
    actions = {
        str(item.get("action") or "modify")
        for item in files
        if isinstance(item, dict)
    }
    if actions == {"create"}:
        return "Added"
    if actions == {"delete"}:
        return "Deleted"

    return "Edited"


def render_tool_start_trace(
    name: str,
    arguments: dict[str, typing.Any]
) -> str:
    """渲染普通工具开始执行前的轨迹行。"""
    args = arguments if isinstance(arguments, dict) else {}
    summary = _short_text(args, 100)
    detail  = f" {summary}" if summary else ""
    return f"• Running {name}{detail}"


def is_native_coding_trace_tool(name: str) -> bool:
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
        root = str(data.get("root") or "").strip()
        return _trace_preview_from_lines([f"root={root}"] if root else [])

    if name == "workspace_list_file":
        files = data.get("files")
        if isinstance(files, list):
            lines = []
            for item in files:
                if not isinstance(item, dict):
                    continue
                path = str(item.get("path") or "").strip()
                kind = str(item.get("file_kind") or "").strip()
                row = f"{kind} {path}".strip()
                if row:
                    lines.append(row)
            return _trace_preview_from_lines(lines)

    if name == "workspace_read_file":
        return _trace_preview_from_lines(_normalize_preview_lines(data.get("content")))

    if name == "workspace_search":
        matches = data.get("matches")
        if isinstance(matches, list):
            lines = []
            for item in matches:
                if isinstance(item, dict):

                    path = str(item.get("path") or "")
                    line = str(item.get("line") or "")
                    text = _short_text(item.get("text"), MAX_PREVIEW_WIDTH)
                    loc  = f"{path}:{line}" if line else path
                    row  = f"{loc} {text}".strip()

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

                index       = item.get("index")
                tool        = str(item.get("tool") or "").strip()
                ok          = "ok" if item.get("ok") else "failed"
                result      = item.get("result") if isinstance(item.get("result"), dict) else {}
                result_data = _result_payload(result)
                detail      = ""

                if tool == "workspace_read_file":
                    detail = str(result_data.get("path") or "").strip()
                elif tool == "workspace_list_file":
                    count = _count_from_payload(result_data, "files", "file_count")
                    detail = f"{count} files" if isinstance(count, int) else ""
                elif tool == "workspace_search":
                    count = _count_from_payload(result_data, "matches", "match_count")
                    detail = f"{count} matches" if isinstance(count, int) else ""
                elif tool == "workspace_root":
                    detail = str(result_data.get("root") or "").strip()

                prefix = f"{index}: " if index is not None else ""
                suffix = f" {detail}" if detail else ""
                lines.append(f"{prefix}{tool} {ok}{suffix}".strip())

            return _trace_preview_from_lines(lines)

    if name == "workspace_write_file":
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
        path = str(data.get("path") or "").strip()
        size = _format_size(data.get("bytes"))
        sha  = _short_sha(data.get("sha256"))

        return _trace_preview_from_lines(_summary_lines(
            ("file", path),
            ("removed", size),
            ("sha256", sha)
        ))

    if name == "workspace_apply_patch":
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
        preview_lines = _unified_patch_preview_lines(args.get("patch"))
        if failed:
            prefix = _patch_failure_diagnostic_lines(data)
            if preview_lines:
                return _trace_code_preview_from_lines([*prefix, *preview_lines])
            return _trace_preview_from_lines(prefix)
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

        stdout_source = data.get("git_status") if name == "git_status" else data.get("stdout")
        lines         = _normalize_preview_lines(stdout_source)
        err_lines     = _normalize_preview_lines(data.get("stderr"))

        if lines and err_lines:
            lines.extend(err_lines)
        elif err_lines:
            lines = err_lines
        if not lines and name == "shell_exec" and data.get("exit_code") is not None:
            lines = [f"exit_code={data.get('exit_code')}"]
        if not lines and name == "git_diff" and data.get("ok") is True:
            lines = ["No tracked changes in git diff"]

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

    if name == "rollback_run":

        lines  = []
        sid    = str(data.get("session_id") or "").strip()
        run_id = str(data.get("run_id") or "").strip()
        status = _status_from_payload(data)

        if sid:
            lines.append(f"session_id={sid}")
        if run_id:
            lines.append(f"run_id={run_id}")
        if status:
            lines.append(f"status={status}")
        if data.get("elapsed_ms") is not None:
            lines.append(f"elapsed_ms={data.get('elapsed_ms')}")

        return _trace_preview_from_lines(lines)

    return TracePreview()


def render_tool_trace(
    name: str,
    arguments: dict[str, typing.Any],
    *,
    ok: bool,
    data: typing.Any = None,
    cost_ms: int | None = None,
    before_exists: typing.Any = MISSING,
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

        query = _short_text(args.get("query"), 80)
        if payload.get("skipped") and payload.get("reason") == "query_empty":
            return "• Skipped empty search"

        matches = payload.get("matches")
        total   = len(matches) if isinstance(matches, list) else None
        detail  = f" ({total} matches)" if isinstance(total, int) else ""

        return f"• Searched \"{query}\"{detail}{suffix}"

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
                detail += f", {fail_count} failed"
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

        command = _command_text(payload.get("command") or args.get("command"))
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

    if name == "rollback_run":

        sid    = _session_id_from_payload(payload, args)
        detail = f" {sid}" if sid else ""

        return f"• Rolled back run{detail}{suffix}"

    summary = _short_text(args, 100)
    detail  = f" {summary}" if summary else ""

    return f"• Ran {name}{detail}{suffix}"


if __name__ == '__main__':
    pass
