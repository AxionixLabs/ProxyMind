# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass
from pathlib import Path

MISSING = object()

TITLE_STYLE   = "bold #D7E7FF"
RUNNING_STYLE = "bold #8FB8FF"
PREVIEW_STYLE = "dim #8FA4B8"
ERROR_STYLE   = "bold #FF7A7A"

MAX_PREVIEW_LINES = 8
SCREEN_PREVIEW_LINES = 5
MAX_PREVIEW_WIDTH = 120

NATIVE_CODING_TRACE_TOOLS = {
    "workspace_root",
    "workspace_read_file",
    "workspace_list_files",
    "workspace_search_text",
    "native_parallel_read",
    "repo_map",
    "repo_find_symbol",
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
    "rollback_run",
    "native_plan",
    "native_coding_loop",
    "native_repair_loop",
    "record_sandbox_result",
    "native_coding_session"
}


@dataclass(frozen=True, slots=True)
class TracePreview(object):
    full: str = ""
    screen: str = ""
    omitted_lines: int = 0


def _short_text(value: typing.Any, limit: int = 120) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[:max(0, limit - 3)]}..."


def _short_line(value: typing.Any, limit: int = 120) -> str:
    text = str(value or "").rstrip()
    if len(text) <= limit:
        return text
    return f"{text[:max(0, limit - 3)]}..."


def _normalize_preview_lines(value: typing.Any) -> list[str]:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    if not text:
        return []
    return text.split("\n")


def _format_preview_lines(lines: list[str], *, max_lines: int) -> tuple[str, int]:
    clipped = [
        _short_line(line, MAX_PREVIEW_WIDTH)
        for line in lines[:max_lines]
    ]
    omitted = max(0, len(lines) - max_lines)
    if omitted:
        clipped.append(f"… +{omitted} lines")
    return "\n".join(clipped), omitted


def _preview_text(value: typing.Any, *, max_lines: int = MAX_PREVIEW_LINES) -> str:
    screen, _ = _format_preview_lines(_normalize_preview_lines(value), max_lines=max_lines)
    return screen


def _trace_preview_from_lines(lines: list[str]) -> TracePreview:
    full, _ = _format_preview_lines(lines, max_lines=MAX_PREVIEW_LINES)
    screen, omitted = _format_preview_lines(lines, max_lines=SCREEN_PREVIEW_LINES)
    return TracePreview(full=full, screen=screen, omitted_lines=omitted)


def _result_payload(data: typing.Any) -> dict[str, typing.Any]:
    if not isinstance(data, dict):
        return {}

    results = data.get("results")
    if isinstance(results, list):
        for item in results:
            if not isinstance(item, dict):
                continue
            item_data = item.get("data")
            if isinstance(item_data, dict):
                return item_data

    nested = data.get("data")
    if isinstance(nested, dict):
        return nested

    return data


def _path_from_args(args: dict[str, typing.Any]) -> str:
    return str(args.get("path") or ".").strip() or "."


def local_path_exists(arguments: dict[str, typing.Any]) -> typing.Any:
    """Return whether the path argument exists, or MISSING when it cannot be checked."""
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
    if isinstance(command, list):
        return " ".join(str(item) for item in command)
    return str(command or "").strip()


def _count_from_payload(payload: dict[str, typing.Any], key: str, count_key: str) -> typing.Optional[int]:
    items = payload.get(key)
    if isinstance(items, list):
        return len(items)
    value = payload.get(count_key)
    return value if isinstance(value, int) else None


def _session_id_from_payload(payload: dict[str, typing.Any], args: dict[str, typing.Any]) -> str:
    return str(payload.get("session_id") or args.get("session_id") or "").strip()


def _status_from_payload(payload: dict[str, typing.Any]) -> str:
    status = str(payload.get("status") or payload.get("repair_status") or payload.get("next_action") or "").strip()
    if status:
        return status
    if payload.get("ok") is True:
        return "ok"
    if payload.get("ok") is False:
        return "failed"
    return ""


def _line_delta_from_content(content: typing.Any) -> tuple[int, int]:
    text = str(content or "")
    if not text:
        return 0, 0
    return len(text.splitlines()) or 1, 0


def _line_delta_from_patch_args(args: dict[str, typing.Any]) -> tuple[int, int]:
    old_lines = str(args.get("old_text") or "").splitlines()
    new_lines = str(args.get("new_text") or "").splitlines()
    replacements = max(1, int(args.get("expected_replacements") or 1))
    return (len(new_lines) or 1) * replacements, (len(old_lines) or 1) * replacements


def _line_delta_from_unified_files(data: dict[str, typing.Any]) -> tuple[int, int]:
    added = data.get("added_lines")
    removed = data.get("removed_lines")
    if isinstance(added, int) or isinstance(removed, int):
        return int(added or 0), int(removed or 0)
    files = data.get("files")
    if not isinstance(files, list):
        return 0, 0
    total_added = 0
    total_removed = 0
    for item in files:
        if not isinstance(item, dict):
            continue
        total_added += int(item.get("added_lines") or 0)
        total_removed += int(item.get("removed_lines") or 0)
    return total_added, total_removed


def _format_delta(added: int, removed: int) -> str:
    if added <= 0 and removed <= 0:
        return ""
    return f" (+{max(0, added)} -{max(0, removed)})"


def _short_sha(value: typing.Any) -> str:
    text = str(value or "").strip()
    return text[:12] if text else ""


def _format_size(value: typing.Any) -> str:
    try:
        size = int(value)
    except (TypeError, ValueError):
        return ""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _summary_lines(*items: tuple[str, typing.Any]) -> list[str]:
    lines: list[str] = []
    for label, value in items:
        text = str(value or "").strip()
        if text:
            lines.append(f"{label}: {text}")
    return lines


def _hunk_label(value: typing.Any) -> str:
    try:
        count = int(value)
    except (TypeError, ValueError):
        return ""
    suffix = "hunk" if count == 1 else "hunks"
    return f"{count} {suffix}"


def _file_action_from_args(args: dict[str, typing.Any], before_exists: typing.Any) -> str:
    if before_exists is False:
        return "Added"
    if before_exists is True:
        return "Edited"
    if args.get("overwrite") is False:
        return "Added"
    return "Edited"


def _unified_action(files: typing.Any) -> str:
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
    arguments: dict[str, typing.Any],
    *,
    before_exists: typing.Any = MISSING,
) -> str:
    """Render one Codex-style trace line before a tool starts."""
    args = arguments if isinstance(arguments, dict) else {}

    if name == "workspace_root":
        return "• Checking workspace root"

    if name == "workspace_read_file":
        return f"• Reading {_path_from_args(args)}"

    if name == "workspace_list_files":
        return f"• Listing {_path_from_args(args)}"

    if name == "workspace_search_text":
        return f"• Searching \"{_short_text(args.get('query'), 80)}\""

    if name == "native_parallel_read":
        items = args.get("items")
        count = len(items) if isinstance(items, list) else 0
        detail = f" ({count} items)" if count else ""
        return f"• Reading context{detail}"

    if name == "repo_map":
        return f"• Mapping repo {_path_from_args(args)}"

    if name == "repo_find_symbol":
        return f"• Finding symbol \"{_short_text(args.get('query'), 80)}\""

    if name == "workspace_write_file":
        action = "Adding" if before_exists is False or args.get("overwrite") is False else "Editing"
        return f"• {action} {_path_from_args(args)}"

    if name == "workspace_copy_file":
        source = str(args.get("source_path") or "").strip()
        target = str(args.get("target_path") or "").strip()
        return f"• Copying {source} -> {target}".rstrip()

    if name == "workspace_move_file":
        source = str(args.get("source_path") or "").strip()
        target = str(args.get("target_path") or "").strip()
        return f"• Moving {source} -> {target}".rstrip()

    if name == "workspace_delete_file":
        return f"• Deleting {_path_from_args(args)}"

    if name == "workspace_apply_patch":
        return f"• Editing {_path_from_args(args)}"

    if name == "workspace_apply_unified_patch":
        return "• Applying patch"

    if name == "shell_exec":
        command = _command_text(args.get("command"))
        return f"• Running {command}".rstrip()

    if name in {"git_status", "git_diff", "change_summary"}:
        return f"• Running {name}"

    if name == "rollback_run":
        sid = str(args.get("session_id") or "").strip()
        detail = f" {sid}" if sid else ""
        return f"• Rolling back run{detail}"

    if name == "native_plan":
        action = str(args.get("action") or "get").strip() or "get"
        return f"• Updating native plan" if action == "update" else "• Reading native plan"

    if name == "native_coding_loop":
        return "• Running native coding loop"

    if name == "native_repair_loop":
        return "• Running native repair loop"

    if name == "record_sandbox_result":
        command = _command_text(args.get("command"))
        detail = f" {command}" if command else ""
        return f"• Recording sandbox result{detail}"

    if name == "native_coding_session":
        sid = str(args.get("session_id") or "").strip()
        return f"• Reading native session {sid}".rstrip()

    summary = _short_text(args, 100)
    detail = f" {summary}" if summary else ""
    return f"• Running {name}{detail}"


def is_native_coding_trace_tool(name: str) -> bool:
    return name in NATIVE_CODING_TRACE_TOOLS


def render_tool_trace_parts(
    title: str,
    *,
    preview: typing.Optional[typing.Union[str, TracePreview]] = None,
    ok: bool = True
) -> list[dict[str, typing.Optional[str]]]:
    parts: list[dict[str, typing.Optional[str]]] = [
        {"text": title, "style": TITLE_STYLE if ok else ERROR_STYLE}
    ]
    preview_text = preview.screen if isinstance(preview, TracePreview) else _preview_text(preview)
    if preview_text:
        parts.extend([
            {"text": "\n", "style": None},
            {"text": "└ ", "style": PREVIEW_STYLE},
            {"text": preview_text.replace("\n", "\n  "), "style": PREVIEW_STYLE},
        ])
    return parts


def render_tool_result_preview(
    name: str,
    data: typing.Any = None
) -> TracePreview:
    data = _result_payload(data)
    if not data:
        return TracePreview()

    if name == "workspace_root":
        root = str(data.get("root") or "").strip()
        return _trace_preview_from_lines([f"root={root}"] if root else [])

    if name == "workspace_read_file":
        return _trace_preview_from_lines(_normalize_preview_lines(data.get("content")))

    if name == "workspace_list_files":
        items = data.get("items")
        if isinstance(items, list):
            lines: list[str] = []
            for item in items:
                if isinstance(item, dict):
                    kind = str(item.get("kind") or "")
                    path = str(item.get("path") or "")
                    line = f"{kind} {path}".strip()
                    if line:
                        lines.append(line)
            return _trace_preview_from_lines(lines)

    if name == "workspace_search_text":
        matches = data.get("matches")
        if isinstance(matches, list):
            lines = []
            for item in matches:
                if isinstance(item, dict):
                    path = str(item.get("path") or "")
                    line = str(item.get("line") or "")
                    text = _short_text(item.get("text"), MAX_PREVIEW_WIDTH)
                    row = f"{path}:{line} {text}".strip()
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
                index = item.get("index")
                tool = str(item.get("tool") or "").strip()
                ok = "ok" if item.get("ok") else "failed"
                result = item.get("result") if isinstance(item.get("result"), dict) else {}
                result_data = _result_payload(result)
                detail = ""
                if tool == "workspace_read_file":
                    detail = str(result_data.get("path") or "").strip()
                elif tool == "workspace_list_files":
                    count = _count_from_payload(result_data, "items", "count")
                    detail = f"{count} items" if isinstance(count, int) else ""
                elif tool == "workspace_search_text":
                    count = _count_from_payload(result_data, "matches", "match_count")
                    detail = f"{count} matches" if isinstance(count, int) else ""
                elif tool == "workspace_root":
                    detail = str(result_data.get("root") or "").strip()
                prefix = f"{index}: " if index is not None else ""
                suffix = f" {detail}" if detail else ""
                lines.append(f"{prefix}{tool} {ok}{suffix}".strip())
            return _trace_preview_from_lines(lines)

    if name == "repo_map":
        symbols = data.get("symbols")
        files = data.get("files")
        lines = []
        if isinstance(symbols, list):
            for item in symbols:
                if not isinstance(item, dict):
                    continue
                path = str(item.get("path") or "").strip()
                line = str(item.get("line") or "").strip()
                kind = str(item.get("kind") or "").strip()
                name_text = str(item.get("qualified_name") or item.get("name") or "").strip()
                row = f"{path}:{line} {kind} {name_text}".strip()
                if row:
                    lines.append(row)
        elif isinstance(files, list):
            for item in files:
                if isinstance(item, dict):
                    path = str(item.get("path") or "").strip()
                    lang = str(item.get("language") or "").strip()
                    symbols_count = item.get("symbol_count")
                    imports_count = item.get("import_count")
                    parts = [path]
                    if lang:
                        parts.append(lang)
                    if symbols_count is not None:
                        parts.append(f"symbols={symbols_count}")
                    if imports_count is not None:
                        parts.append(f"imports={imports_count}")
                    line = " ".join(str(part) for part in parts if str(part))
                    if line:
                        lines.append(line)
        return _trace_preview_from_lines(lines)

    if name == "repo_find_symbol":
        matches = data.get("matches")
        if isinstance(matches, list):
            lines = []
            for item in matches:
                if not isinstance(item, dict):
                    continue
                path = str(item.get("path") or "").strip()
                line = str(item.get("line") or "").strip()
                kind = str(item.get("kind") or "").strip()
                name_text = str(item.get("qualified_name") or item.get("name") or "").strip()
                signature = _short_text(item.get("signature"), 80)
                row = f"{path}:{line} {kind} {name_text}".strip()
                if signature:
                    row = f"{row} — {signature}"
                if row:
                    lines.append(row)
            return _trace_preview_from_lines(lines)

    if name == "workspace_write_file":
        path = str(data.get("path") or "").strip()
        size = _format_size(data.get("bytes"))
        sha = _short_sha(data.get("sha256"))
        return _trace_preview_from_lines(_summary_lines(
            ("file", path),
            ("size", size),
            ("sha256", sha),
        ))

    if name == "workspace_copy_file":
        source = str(data.get("source_path") or "").strip()
        target = str(data.get("target_path") or "").strip()
        size = _format_size(data.get("bytes"))
        sha = _short_sha(data.get("sha256"))
        return _trace_preview_from_lines(_summary_lines(
            ("from", source),
            ("to", target),
            ("size", size),
            ("sha256", sha),
        ))

    if name == "workspace_move_file":
        source = str(data.get("source_path") or "").strip()
        target = str(data.get("target_path") or "").strip()
        size = _format_size(data.get("bytes"))
        sha = _short_sha(data.get("sha256"))
        return _trace_preview_from_lines(_summary_lines(
            ("from", source),
            ("to", target),
            ("size", size),
            ("sha256", sha),
        ))

    if name == "workspace_delete_file":
        path = str(data.get("path") or "").strip()
        size = _format_size(data.get("bytes"))
        sha = _short_sha(data.get("sha256"))
        return _trace_preview_from_lines(_summary_lines(
            ("file", path),
            ("removed", size),
            ("sha256", sha),
        ))

    if name == "workspace_apply_patch":
        path = str(data.get("path") or "").strip()
        replacements = data.get("replacements")
        sha = _short_sha(data.get("sha256"))
        return _trace_preview_from_lines(_summary_lines(
            ("file", path),
            ("replacements", replacements),
            ("sha256", sha),
        ))

    if name == "workspace_apply_unified_patch":
        files = data.get("files")
        if isinstance(files, list):
            lines = []
            for item in files:
                if not isinstance(item, dict):
                    continue
                action = str(item.get("action") or "modify").strip() or "modify"
                path = str(item.get("path") or "").strip()
                hunk_text = _hunk_label(item.get("hunks"))
                sha = _short_sha(item.get("sha256"))
                line = f"{action} {path}".strip()
                details = []
                if hunk_text:
                    details.append(hunk_text)
                added = item.get("added_lines")
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
        lines = _normalize_preview_lines(data.get("stdout"))
        err_lines = _normalize_preview_lines(data.get("stderr"))
        if lines and err_lines:
            lines.extend(err_lines)
        elif err_lines:
            lines = err_lines
        if not lines and name == "shell_exec" and data.get("exit_code") is not None:
            lines = [f"exit_code={data.get('exit_code')}"]
        return _trace_preview_from_lines(lines)

    if name == "change_summary":
        lines = _normalize_preview_lines(data.get("summary") or data.get("diff") or data.get("status"))
        return _trace_preview_from_lines(lines)

    if name in {
        "rollback_run",
        "native_plan",
        "native_coding_loop",
        "native_repair_loop",
        "record_sandbox_result",
        "native_coding_session"
    }:
        lines = []
        sid = str(data.get("session_id") or "").strip()
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
    """Render one Codex-style factual trace line for a completed tool call."""
    args = arguments if isinstance(arguments, dict) else {}
    payload = _result_payload(data)
    suffix = "" if ok else " failed"

    if name == "workspace_root":
        root = str(payload.get("root") or "").strip()
        return f"• Root {root}".rstrip()

    if name == "workspace_read_file":
        path = str(payload.get("path") or _path_from_args(args))
        return f"• Read {path}{suffix}"

    if name == "workspace_list_files":
        path = str(payload.get("path") or _path_from_args(args))
        count = payload.get("items")
        total = len(count) if isinstance(count, list) else payload.get("count")
        detail = f" ({total} items)" if isinstance(total, int) else ""
        return f"• Listed {path}{detail}{suffix}"

    if name == "workspace_search_text":
        query = _short_text(args.get("query"), 80)
        matches = payload.get("matches")
        total = len(matches) if isinstance(matches, list) else None
        detail = f" ({total} matches)" if isinstance(total, int) else ""
        return f"• Searched \"{query}\"{detail}{suffix}"

    if name == "native_parallel_read":
        total = payload.get("total")
        ok_count = payload.get("ok_count")
        fail_count = payload.get("fail_count")
        detail = ""
        if isinstance(total, int):
            detail = f" ({total} items"
            if isinstance(ok_count, int):
                detail += f", {ok_count} ok"
            if isinstance(fail_count, int) and fail_count:
                detail += f", {fail_count} failed"
            detail += ")"
        return f"• Read context{detail}{suffix}"

    if name == "repo_map":
        path = str(payload.get("path") or _path_from_args(args))
        file_count = _count_from_payload(payload, "files", "file_count")
        symbol_count = _count_from_payload(payload, "symbols", "symbol_count")
        import_count = _count_from_payload(payload, "imports", "import_count")
        details = []
        if isinstance(file_count, int):
            details.append(f"{file_count} files")
        if isinstance(symbol_count, int):
            details.append(f"{symbol_count} symbols")
        if isinstance(import_count, int):
            details.append(f"{import_count} imports")
        detail = f" ({', '.join(details)})" if details else ""
        return f"• Mapped repo {path}{detail}{suffix}"

    if name == "repo_find_symbol":
        query = _short_text(args.get("query") or payload.get("query"), 80)
        matches = payload.get("matches")
        total = len(matches) if isinstance(matches, list) else payload.get("match_count")
        detail = f" ({total} matches)" if isinstance(total, int) else ""
        return f"• Found symbol \"{query}\"{detail}{suffix}"

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
        rc = payload.get("exit_code")
        elapsed = payload.get("elapsed_ms", cost_ms)
        status = ""
        if rc is not None:
            status += f" exit_code={rc}"
        if elapsed is not None:
            status += f" elapsed_ms={elapsed}"
        return f"• Ran {command}{status}{suffix}".rstrip()

    if name in {"git_status", "git_diff", "change_summary"}:
        return f"• Ran {name}{suffix}"

    if name == "rollback_run":
        sid = _session_id_from_payload(payload, args)
        detail = f" {sid}" if sid else ""
        return f"• Rolled back run{detail}{suffix}"

    if name == "native_plan":
        action = str(args.get("action") or "get").strip() or "get"
        verb = "Updated" if action == "update" else "Read"
        sid = _session_id_from_payload(payload, args)
        detail = f" {sid}" if sid else ""
        return f"• {verb} native plan{detail}{suffix}"

    if name == "native_coding_loop":
        sid = _session_id_from_payload(payload, args)
        status = _status_from_payload(payload)
        details = []
        if sid:
            details.append(f"session_id={sid}")
        if status:
            details.append(f"status={status}")
        detail = f" ({', '.join(details)})" if details else ""
        return f"• Ran native coding loop{detail}{suffix}"

    if name == "native_repair_loop":
        sid = _session_id_from_payload(payload, args)
        status = _status_from_payload(payload)
        details = []
        if sid:
            details.append(f"session_id={sid}")
        if status:
            details.append(f"status={status}")
        detail = f" ({', '.join(details)})" if details else ""
        return f"• Ran native repair loop{detail}{suffix}"

    if name == "record_sandbox_result":
        command = _command_text(payload.get("command") or args.get("command"))
        rc = payload.get("exit_code", args.get("exit_code"))
        status = f" exit_code={rc}" if rc is not None else ""
        return f"• Recorded sandbox result {command}{status}{suffix}".rstrip()

    if name == "native_coding_session":
        sid = _session_id_from_payload(payload, args)
        detail = f" {sid}" if sid else ""
        return f"• Read native session{detail}{suffix}"

    summary = _short_text(args, 100)
    detail = f" {summary}" if summary else ""
    return f"• Ran {name}{detail}{suffix}"


if __name__ == '__main__':
    pass
