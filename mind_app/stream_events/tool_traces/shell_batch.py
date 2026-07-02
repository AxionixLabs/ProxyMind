# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_core.design.status.elapsed import format_elapsed
from mind_app.stream_events.command_preview import command_preview
from .common import (
    MAX_PREVIEW_WIDTH,
    TracePreview,
    _result_payload,
    _short_line,
    _short_text
)
from .native_helpers import failure_summary
from .shell_errors import (
    shell_error_compact_summary,
    shell_output_lines
)


def _shell_batch_item_payload(item: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """提取 shell batch 子项的结果 data。"""
    result = item.get("result") if isinstance(item, dict) else None
    return _result_payload(result) if isinstance(result, dict) else {}


def shell_batch_trace_title(
    payload: dict[str, typing.Any],
    *,
    cost_ms: int | None = None
) -> str:
    """生成 shell batch 的聚合标题。"""
    total      = _int_or_none(payload.get("total"))
    ok_count   = _int_or_none(payload.get("ok_count"))
    fail_count = _int_or_none(payload.get("fail_count"))

    if total is None:
        results = payload.get("results")
        total   = len(results) if isinstance(results, list) else 0

    if ok_count is None:
        results = payload.get("results")
        if isinstance(results, list):
            ok_count = sum(1 for item in results if isinstance(item, dict) and bool(item.get("ok")))

    if fail_count is None and ok_count is not None:
        fail_count = max(0, total - ok_count)

    parts = [f"• Ran {total} {_plural(total, 'command', 'commands')}"]

    if ok_count is not None:
        parts.append(f"{ok_count} ok")
    if fail_count:
        parts.append(f"{fail_count} failed")
    if isinstance(cost_ms, int) and cost_ms >= 0:
        parts.append(format_elapsed(cost_ms / 1000.0))

    return " · ".join(parts)


def shell_batch_tree_preview(data: typing.Any) -> TracePreview:
    """生成 shell batch 的树形结果预览。"""
    payload = _result_payload(data)
    results = payload.get("results")

    if not isinstance(results, list) or not results:
        return TracePreview()

    full_lines = _shell_batch_tree_lines(results)

    screen_lines, omitted = _shell_batch_screen_lines(results)

    return TracePreview(
        full="\n".join(full_lines),
        screen="\n".join(screen_lines),
        omitted_lines=omitted,
        kind="tree"
    )


def _shell_batch_tree_lines(
    results: list[typing.Any],
    *,
    omitted_items: int = 0
) -> list[str]:
    """把 shell batch 结果转换为树形行。"""
    lines: list[str] = []

    visible_results = [item for item in results if isinstance(item, dict)]
    if omitted_items:
        visible_results.append({"_omitted_items": omitted_items})

    for index, item in enumerate(visible_results):
        if not isinstance(item, dict):
            continue

        is_last = index == len(visible_results) - 1

        item_connector   = "└─" if is_last else "├─"
        detail_connector = "   └─" if is_last else "│  └─"

        omitted = item.get("_omitted_items")
        if isinstance(omitted, int) and omitted > 0:
            lines.append(f"{item_connector} … +{omitted} {_plural(omitted, 'command', 'commands')}")
            continue

        item_payload = _shell_batch_item_payload(item)

        ok     = bool(item.get("ok"))
        mark   = "✓" if ok else "✗"
        label  = _shell_batch_tree_label(item, item_payload)
        detail = _shell_batch_tree_detail(item, item_payload, ok=ok)

        lines.append(f"{item_connector} {mark} {label}".rstrip())
        if detail and (ok or _normalize_tree_text(detail) != _normalize_tree_text(label)):
            lines.append(f"{detail_connector} {detail}".rstrip())

    return lines


def _normalize_tree_text(value: typing.Any) -> str:
    """归一化树节点文本，用于避免标题和详情重复展示。"""
    return " ".join(str(value or "").split())


def _shell_batch_tree_label(
    item: dict[str, typing.Any],
    payload: dict[str, typing.Any]
) -> str:
    """提取 shell batch 树节点标题。"""
    args    = item.get("args") if isinstance(item.get("args"), dict) else {}
    tool    = str(item.get("tool") or "").strip()
    command = command_preview(args.get("command", payload.get("command"))).title

    if tool in {"", "shell_command"} and command:
        return _short_text(command or "shell_command", 80)

    target = _short_text(args, 80)
    return f"{tool or 'item'} {target}".strip()


def _shell_batch_tree_detail(
    item: dict[str, typing.Any],
    payload: dict[str, typing.Any],
    *,
    ok: bool
) -> str:
    """提取 shell batch 树节点详情。"""
    args    = item.get("args") if isinstance(item.get("args"), dict) else {}
    command = command_preview(args.get("command", payload.get("command"))).title

    if ok:
        return _short_line(_shell_batch_output_summary(payload) or "(no output)", MAX_PREVIEW_WIDTH)

    message = (
        shell_error_compact_summary(payload.get("stderr"))
        or shell_error_compact_summary(payload.get("stdout"))
        or failure_summary(payload)
    )
    if message:
        return _short_line(message, MAX_PREVIEW_WIDTH)

    return _short_line(command, MAX_PREVIEW_WIDTH)


def _shell_batch_output_summary(payload: dict[str, typing.Any]) -> str:
    """生成成功命令的单行输出摘要。"""
    stdout = _first_output_line(payload.get("stdout"))
    if stdout:
        return stdout

    stderr = _first_output_line(payload.get("stderr"))
    if stderr:
        return f"stderr: {stderr}"

    return ""


def _first_output_line(value: typing.Any) -> str:
    """返回输出中的首个非空行，并提示后续省略行数。"""
    lines = [line.strip() for line in shell_output_lines(value)]
    if not lines:
        return ""

    suffix = f" … +{len(lines) - 1} lines" if len(lines) > 1 else ""
    return f"{lines[0]}{suffix}"


def _shell_batch_screen_lines(results: list[typing.Any]) -> tuple[list[str], int]:
    """按 item 数限制 shell batch 屏幕树高度，并优先展示失败项。"""
    max_items = 6

    items = [item for item in results if isinstance(item, dict)]
    if len(items) <= max_items:
        return _shell_batch_tree_lines(items), 0

    selected: set[int] = set(range(min(2, len(items))))

    for index, item in enumerate(items):
        if len(selected) >= max_items:
            break
        if not _shell_batch_item_ok(item):
            selected.add(index)

    for index in range(len(items)):
        if len(selected) >= max_items:
            break
        selected.add(index)

    selected_indexes = sorted(selected)
    entries: list[dict[str, typing.Any]] = []
    previous = -1

    for index in selected_indexes:
        skipped = index - previous - 1
        if skipped > 0:
            entries.append({"_omitted_items": skipped})
        entries.append(items[index])
        previous = index

    trailing = len(items) - previous - 1
    if trailing > 0:
        entries.append({"_omitted_items": trailing})

    omitted = len(items) - len(selected)

    return _shell_batch_tree_lines(entries), omitted


def _shell_batch_item_ok(item: dict[str, typing.Any]) -> bool:
    """判断 shell batch 子项是否成功。"""
    return bool(item.get("ok"))


def _plural(count: int, singular: str, plural: str) -> str:
    return singular if count == 1 else plural


def _int_or_none(value: typing.Any) -> int | None:
    return value if isinstance(value, int) else None


if __name__ == '__main__':
    pass
