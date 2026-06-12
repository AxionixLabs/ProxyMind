# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from .common import (
    _result_payload,
    _short_text
)


def _shell_call_item_payload(item: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """提取 shell calls 子项的结果 data。"""
    result = item.get("result") if isinstance(item, dict) else None
    return _result_payload(result) if isinstance(result, dict) else {}


def _shell_call_reason_label(reason: typing.Any) -> str:
    """把底层失败 reason 压缩成适合 trace 的短标签。"""
    text = str(reason or "").strip()
    if text == "path_outside_workspace":
        return "outside"
    if text == "tool_not_allowed":
        return "not_allowed"
    if text == "shell_call_item_failed":
        return "error"

    return text or "failed"


def _shell_call_item_target(
    item: dict[str, typing.Any],
    payload: dict[str, typing.Any]
) -> str:
    """读取 shell call 子项最有用的目标描述。"""
    args = item.get("args") if isinstance(item.get("args"), dict) else {}
    tool = str(item.get("tool") or "").strip()

    if tool == "shell_command":
        command = args.get("command", payload.get("command"))
        return _short_text(command, 100)

    return _short_text(args, 100)


def _shell_call_failure_summary(payload: dict[str, typing.Any]) -> str:
    """生成 shell calls 标题里的失败原因摘要。"""
    reasons = payload.get("failure_reasons")
    if not isinstance(reasons, dict) or not reasons:
        fail_count = payload.get("fail_count")
        return f"{fail_count} failed" if isinstance(fail_count, int) and fail_count else ""

    parts = []
    for reason, count in sorted(reasons.items(), key=lambda item: str(item[0])):
        try:
            number = int(count)
        except (TypeError, ValueError):
            number = 0
        if number <= 0:
            continue
        parts.append(f"{number} {_shell_call_reason_label(reason)}")

    return ", ".join(parts)


if __name__ == '__main__':
    pass
