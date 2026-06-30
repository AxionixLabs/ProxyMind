# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from .common import (
    _normalize_preview_lines,
    _short_line,
    _summary_lines
)
from .native_helpers import _diagnostic_sequence_lines


def _line_delta_from_patch_files(data: dict[str, typing.Any]) -> tuple[int, int]:
    """从 patch 结果中读取新增和删除行数。"""
    added   = data.get("added_lines")
    removed = data.get("removed_lines")

    if isinstance(added, int) or isinstance(removed, int):
        return int(added or 0), int(removed or 0)

    files = data.get("files")
    if not isinstance(files, list):
        return 0, 0

    total_added: int   = 0
    total_removed: int = 0

    for item in files:
        if not isinstance(item, dict):
            continue
        total_added += int(item.get("added_lines") or 0)
        total_removed += int(item.get("removed_lines") or 0)

    return total_added, total_removed


def _patch_error_diagnostic_lines(data: dict[str, typing.Any]) -> list[str]:
    """生成 patch 异常时优先展示的诊断摘要。"""
    lines = _summary_lines(
        ("reason", data.get("reason")),
        ("error", data.get("error")),
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


def _patch_preview_lines(patch: typing.Any) -> list[str]:
    """从严格 apply_patch 文本中提取按文件分组的代码预览行。"""
    groups: list[dict[str, typing.Any]]   = []
    current: dict[str, typing.Any] | None = None

    current_old_line: int = 1
    current_new_line: int = 1

    for raw in _normalize_preview_lines(patch):
        if raw in {"*** Begin Patch", "*** End Patch"}:
            continue

        if raw.startswith("*** Add File: "):
            current = _new_patch_preview_group(groups, raw[len("*** Add File: "):])
            current_old_line = 1
            current_new_line = 1
            continue

        if raw.startswith("*** Update File: "):
            current = _new_patch_preview_group(groups, raw[len("*** Update File: "):])
            current_old_line = 1
            current_new_line = 1
            continue

        if raw.startswith("*** Delete File: "):
            current = _new_patch_preview_group(groups, raw[len("*** Delete File: "):])
            current_old_line = 1
            current_new_line = 1
            continue

        if raw.startswith("*** Move to: "):
            if current is not None:
                current["path"] = str(raw[len("*** Move to: "):] or "").strip()
            continue

        if raw.startswith("@@"):
            _append_patch_preview_line(groups, current, raw)
            continue

        if raw.startswith("*** "):
            continue

        if not raw:
            continue

        marker = raw[0]
        text   = raw[1:] if marker in {" ", "+", "-"} else raw

        if marker == "+":
            _append_patch_preview_line(groups, current, f"{current_new_line:>4} +{text}")
            if current is not None:
                current["added"] = int(current.get("added") or 0) + 1
            current_new_line += 1

        elif marker == "-":
            _append_patch_preview_line(groups, current, f"{current_old_line:>4} -{text}")
            if current is not None:
                current["removed"] = int(current.get("removed") or 0) + 1
            current_old_line += 1

        elif marker == " ":
            _append_patch_preview_line(groups, current, f"{current_new_line:>4}  {text}")
            current_old_line += 1
            current_new_line += 1

    return _flatten_patch_preview_groups(groups)


def _new_patch_preview_group(
    groups: list[dict[str, typing.Any]],
    path: typing.Any
) -> dict[str, typing.Any]:
    """创建一个严格 patch 文件预览分组。"""
    current = {
        "path"    : str(path or "").strip(),
        "added"   : 0,
        "removed" : 0,
        "lines"   : []
    }
    groups.append(current)
    return current


def _append_patch_preview_line(
    groups: list[dict[str, typing.Any]],
    current: dict[str, typing.Any] | None,
    line: str
) -> None:
    """向当前文件分组追加一行；没有文件头时使用匿名分组。"""
    if current is None:
        current = {
            "path"    : "",
            "added"   : 0,
            "removed" : 0,
            "lines"   : []
        }
        groups.append(current)

    lines = current.get("lines")
    if isinstance(lines, list):
        lines.append(line)


def _flatten_patch_preview_groups(groups: list[dict[str, typing.Any]]) -> list[str]:
    """把文件分组压平为可渲染的预览行。"""
    lines: list[str] = []

    for index, group in enumerate(groups):

        path    = str(group.get("path") or "").strip()
        added   = int(group.get("added") or 0)
        removed = int(group.get("removed") or 0)

        if index:
            lines.append("")
        if path:
            lines.append(f"└─ {path} (+{added} -{removed})")
        lines.extend(str(item) for item in group.get("lines") or [])

    return lines


def _error_patch_preview_lines(patch: typing.Any, data: dict[str, typing.Any]) -> list[str]:
    """从异常 patch 中提取相关 hunk 附近的短预览。"""
    hunk_header = str(data.get("hunk_header") or data.get("header") or "").strip()
    if not hunk_header:
        return _patch_preview_lines(patch)[:6]

    raw_lines = _normalize_preview_lines(patch)

    hunk_index = next(
        (index for index, line in enumerate(raw_lines) if line.strip() == hunk_header),
        -1,
    )
    if hunk_index < 0:
        return _patch_preview_lines(patch)[:6]

    start = hunk_index
    while start > 0 and not raw_lines[start].startswith("*** "):
        start -= 1

    end = hunk_index + 1

    body_count = 0

    while end < len(raw_lines):
        line = raw_lines[end]
        if line.startswith(("@@", "*** ")):
            break
        body_count += 1
        end += 1
        if body_count >= 4:
            break

    return _patch_preview_lines("\n".join(raw_lines[start:end]))


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
