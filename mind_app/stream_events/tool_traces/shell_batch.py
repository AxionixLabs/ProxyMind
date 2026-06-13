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
        total = len(results) if isinstance(results, list) else 0
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

        ok     = bool(item.get("ok")) if "ok" in item else bool(item_payload.get("ok"))
        mark   = "✓" if ok else "✗"
        label  = _shell_batch_tree_label(item, item_payload)
        detail = _shell_batch_tree_detail(item, item_payload, ok=ok)

        lines.append(f"{item_connector} {mark} {label}".rstrip())
        if detail:
            lines.append(f"{detail_connector} {detail}".rstrip())

    return lines


def _shell_batch_tree_label(
    item: dict[str, typing.Any],
    payload: dict[str, typing.Any]
) -> str:
    """提取 shell batch 树节点标题。"""
    args    = item.get("args") if isinstance(item.get("args"), dict) else {}
    tool    = str(item.get("tool") or "").strip()
    command = command_preview(args.get("command", payload.get("command"))).title

    if tool in {"", "shell_command"} and command:
        target = _target_from_command(command)
        return _short_text(target or command or "shell_command", 80)

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
        return _short_line(command, MAX_PREVIEW_WIDTH)

    message = failure_summary(payload)
    if message:
        return _short_line(message, MAX_PREVIEW_WIDTH)

    return _short_line(command, MAX_PREVIEW_WIDTH)


def _target_from_command(command: str) -> str:
    """从常见只读命令里提取展示目标。"""
    tokens = _display_tokens(command)
    if not tokens:
        return ""

    for flag in ("-LiteralPath", "-Path"):
        if flag in tokens:
            index = tokens.index(flag)
            if index + 1 < len(tokens):
                return tokens[index + 1]

    for token in tokens[1:]:
        if _looks_like_target(token):
            return token

    return ""


def _display_tokens(command: str) -> list[str]:
    """按显示用途拆分命令 token。"""
    text = str(command or "").strip()
    if not text:
        return []

    tokens: list[str]  = []
    current: list[str] = []

    quote: str    = ""
    escaped: bool = False

    for char in text:
        if escaped:
            current.append(char)
            escaped = False
            continue
        if char == "\\":
            current.append(char)
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = ""
            else:
                current.append(char)
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char.isspace():
            if current:
                tokens.append("".join(current))
                current = []
            continue
        current.append(char)

    if current:
        tokens.append("".join(current))

    return tokens


def _looks_like_target(token: str) -> bool:
    """判断 token 是否适合作为树节点标题。"""
    text = str(token or "").strip()
    if not text or text.startswith("-") or "=" in text:
        return False
    if text in {".", ".."}:
        return True

    return "/" in text or "\\" in text or "." in text


def _shell_batch_screen_lines(results: list[typing.Any]) -> tuple[list[str], int]:
    """按 item 数限制 shell batch 屏幕树高度。"""
    max_items = 5

    items = [item for item in results if isinstance(item, dict)]

    visible = items[:max_items]
    omitted = max(0, len(items) - len(visible))

    return _shell_batch_tree_lines(visible, omitted_items=omitted), omitted


def _plural(count: int, singular: str, plural: str) -> str:
    return singular if count == 1 else plural


def _int_or_none(value: typing.Any) -> int | None:
    return value if isinstance(value, int) else None


if __name__ == '__main__':
    pass
