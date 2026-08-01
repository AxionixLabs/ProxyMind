# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_app.native_coding.edit.parser import PatchParser
from .common import (
    MAX_CODE_PREVIEW_LINES,
    SCREEN_CODE_PREVIEW_LINES,
    TracePreview,
    _format_preview_lines,
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
    """从受支持的补丁文本中提取按文件分组的代码预览行。"""
    return _flatten_patch_preview_groups(_patch_preview_groups(patch))


def _patch_preview(patch: typing.Any) -> TracePreview:
    """生成 patch 预览，屏幕摘要优先保留每个文件节点。"""
    groups     = _patch_preview_groups(patch)
    full_lines = _flatten_patch_preview_groups(groups)

    full, _ = _format_preview_lines(full_lines, max_lines=MAX_CODE_PREVIEW_LINES)

    screen_lines = _screen_patch_preview_groups(groups)

    screen, omitted = _format_preview_lines(screen_lines, max_lines=SCREEN_CODE_PREVIEW_LINES)

    return TracePreview(full=full, screen=screen, omitted_lines=omitted, kind="patch_tree")


def _patch_preview_groups(patch: typing.Any) -> list[dict[str, typing.Any]]:
    """从统一补丁结构中生成文件预览分组。"""
    parsed = PatchParser.parse_patch(str(patch or ""))
    if not parsed["ok"]:
        return []

    groups: list[dict[str, typing.Any]] = []

    for item in parsed["files"]:
        current = _new_patch_preview_group(groups, item.path)

        for hunk in item.hunks:
            current_old_line = hunk.old_start
            current_new_line = hunk.new_start
            _append_patch_preview_line(groups, current, hunk.header)

            for raw in hunk.entries:
                marker = raw.marker
                text   = raw.text

                if marker == "+":
                    _append_patch_preview_line(
                        groups,
                        current,
                        f"{current_new_line:>4} +{text}"
                    )
                    current["added"] = int(current.get("added") or 0) + 1
                    current_new_line += 1

                elif marker == "-":
                    _append_patch_preview_line(
                        groups,
                        current,
                        f"{current_old_line:>4} -{text}"
                    )
                    current["removed"] = int(current.get("removed") or 0) + 1
                    current_old_line += 1

                else:
                    _append_patch_preview_line(
                        groups,
                        current,
                        f"{current_new_line:>4}  {text}"
                    )
                    current_old_line += 1
                    current_new_line += 1

    return groups


def _new_patch_preview_group(
    groups: list[dict[str, typing.Any]],
    path: typing.Any
) -> dict[str, typing.Any]:
    """创建一个补丁文件预览分组。"""
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


def _screen_patch_preview_groups(groups: list[dict[str, typing.Any]]) -> list[str]:
    """生成屏幕预览，避免长文件内容挤掉后续文件节点。"""
    if len(groups) <= 1:
        return _flatten_patch_preview_groups(groups)

    lines: list[str] = []
    total: int       = len(groups)

    detail_limit = 1 if total >= SCREEN_CODE_PREVIEW_LINES // 2 else 2

    for index, group in enumerate(groups):
        path    = str(group.get("path") or "").strip()
        added   = int(group.get("added") or 0)
        removed = int(group.get("removed") or 0)
        body    = [str(item) for item in group.get("lines") or []]

        if index:
            lines.append("")
        if path:
            lines.append(f"└─ {path} (+{added} -{removed})")

        shown = body[:detail_limit]
        lines.extend(shown)

        omitted = len(body) - len(shown)
        if omitted > 0:
            lines.append(f"… +{omitted} lines")

    return lines


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
