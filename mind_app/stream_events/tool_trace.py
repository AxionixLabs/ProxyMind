# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass

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
    "workspace_write_file",
    "workspace_apply_patch",
    "workspace_apply_unified_patch",
    "shell_exec",
    "git_status",
    "git_diff",
    "change_summary"
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


def _command_text(command: typing.Any) -> str:
    if isinstance(command, list):
        return " ".join(str(item) for item in command)
    return str(command or "").strip()


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
    # The current native tool returns file/hunk counts but not per-line diff stats.
    # Keep the trace factual without inventing line counts.
    return 0, 0


def _format_delta(added: int, removed: int) -> str:
    if added <= 0 and removed <= 0:
        return ""
    return f" (+{max(0, added)} -{max(0, removed)})"


def _short_sha(value: typing.Any) -> str:
    text = str(value or "").strip()
    return text[:12] if text else ""


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

    if name == "workspace_write_file":
        action = "Adding" if before_exists is False or args.get("overwrite") is False else "Editing"
        return f"• {action} {_path_from_args(args)}"

    if name == "workspace_apply_patch":
        return f"• Editing {_path_from_args(args)}"

    if name == "workspace_apply_unified_patch":
        return "• Applying patch"

    if name == "shell_exec":
        command = _command_text(args.get("command"))
        return f"• Running {command}".rstrip()

    if name in {"git_status", "git_diff", "change_summary"}:
        return f"• Running {name}"

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

    if name == "workspace_write_file":
        path = str(data.get("path") or "").strip()
        size = data.get("bytes")
        sha = _short_sha(data.get("sha256"))
        parts = []
        if path:
            parts.append(f"path={path}")
        if size is not None:
            parts.append(f"bytes={size}")
        if sha:
            parts.append(f"sha256={sha}")
        return _trace_preview_from_lines([" ".join(parts)] if parts else [])

    if name == "workspace_apply_patch":
        path = str(data.get("path") or "").strip()
        replacements = data.get("replacements")
        sha = _short_sha(data.get("sha256"))
        parts = []
        if path:
            parts.append(f"path={path}")
        if replacements is not None:
            parts.append(f"replacements={replacements}")
        if sha:
            parts.append(f"sha256={sha}")
        return _trace_preview_from_lines([" ".join(parts)] if parts else [])

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

    if name == "workspace_write_file":
        path = str(payload.get("path") or _path_from_args(args))
        added, removed = _line_delta_from_content(args.get("content"))
        action = _file_action_from_args(args, before_exists)
        return f"• {action} {path}{_format_delta(added, removed)}{suffix}"

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

    summary = _short_text(args, 100)
    detail = f" {summary}" if summary else ""
    return f"• Ran {name}{detail}{suffix}"


if __name__ == '__main__':
    pass
