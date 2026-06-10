# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import typing
from .common import (
    _normalize_preview_lines,
    _short_line,
    _summary_lines
)
from .native_helpers import _diagnostic_sequence_lines


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


def _patch_failure_diagnostic_lines(data: dict[str, typing.Any]) -> list[str]:
    """生成 patch 失败时优先展示的诊断摘要。"""
    lines = _summary_lines(
        ("reason", data.get("reason")),
        ("file", data.get("path")),
        ("hunk", data.get("hunk_header") or data.get("header")),
        ("line", data.get("target_line") or data.get("line"))
    )
    lines.extend(
        _diagnostic_sequence_lines(
            "expected", data.get("expected_sequence") or data.get("expected")
        )
    )
    lines.extend(
        _diagnostic_sequence_lines(
            "actual", data.get("actual_sequence") or data.get("actual")
        )
    )

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
    prefix = _summary_lines(
        ("file", args.get("path")),
        ("replacements", args.get("expected_replacements")
        if int(args.get("expected_replacements") or 1) > 1 else None)
    )
    old_lines = _numbered_removed_lines(args.get("old_text"))
    new_lines = _numbered_added_lines(args.get("new_text"))

    if old_lines and new_lines:
        return [*prefix, *old_lines, *new_lines]

    return [*prefix, *(new_lines or old_lines)]


def _unified_patch_preview_lines(patch: typing.Any) -> list[str]:
    """从 unified diff 文本中提取可显示的代码预览行。"""
    lines: list[str] = []

    current_old_line: int = 1
    current_new_line: int = 1
    pending_old_path: str = ""

    for raw in _normalize_preview_lines(patch):
        if raw.startswith("--- "):
            pending_old_path = _patch_display_path(raw[4:])
            continue
        if raw.startswith("+++ "):
            path = _patch_display_path(raw[4:]) or pending_old_path
            if path and path != "/dev/null":
                lines.append(f"file: {path}")
            continue
        if raw.startswith("@@ "):
            match = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", raw)
            if match:
                current_old_line = int(match.group(1))
                current_new_line = int(match.group(2))
            lines.append(raw)
            continue
        if not raw:
            continue

        marker = raw[0]
        text   = raw[1:] if marker in {" ", "+", "-"} else raw

        if marker == "+":
            lines.append(f"{current_new_line:>4} +{text}")
            current_new_line += 1
        elif marker == "-":
            lines.append(f"{current_old_line:>4} -{text}")
            current_old_line += 1
        elif marker == " ":
            lines.append(f"{current_new_line:>4}  {text}")
            current_old_line += 1
            current_new_line += 1

    return lines


def _failed_unified_patch_preview_lines(patch: typing.Any, data: dict[str, typing.Any]) -> list[str]:
    """从失败 patch 中提取失败 hunk 附近的短预览。"""
    hunk_header = str(data.get("hunk_header") or data.get("header") or "").strip()
    if not hunk_header:
        return _unified_patch_preview_lines(patch)[:6]

    raw_lines = _normalize_preview_lines(patch)

    hunk_index = next(
        (index for index, line in enumerate(raw_lines) if line.strip() == hunk_header),
        -1,
    )
    if hunk_index < 0:
        return _unified_patch_preview_lines(patch)[:6]

    start = hunk_index
    while start > 0 and not raw_lines[start].startswith("--- "):
        start -= 1

    end = hunk_index + 1
    body_count = 0

    while end < len(raw_lines):
        line = raw_lines[end]
        if line.startswith(("@@ ", "--- ", "+++ ")):
            break
        body_count += 1
        end += 1
        if body_count >= 4:
            break

    return _unified_patch_preview_lines("\n".join(raw_lines[start:end]))


def _patch_display_path(value: typing.Any) -> str:
    """把 unified diff 文件头路径转换为工作区相对显示路径。"""
    text = str(value or "").strip()
    if text in {"", "/dev/null"}:
        return text
    if text.startswith("a/") or text.startswith("b/"):
        return text[2:]
    return text


def _hunk_label(value: typing.Any) -> str:
    """格式化 hunk 数量。"""
    try:
        count = int(value)
    except (TypeError, ValueError):
        return ""

    suffix = "hunk" if count == 1 else "hunks"
    return f"{count} {suffix}"


if __name__ == '__main__':
    pass
