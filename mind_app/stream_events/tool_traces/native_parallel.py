# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from .common import (
    _result_payload,
    _short_text
)


def _parallel_read_item_payload(item: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """提取 native_parallel_read 子项的结果 data。"""
    result = item.get("result") if isinstance(item, dict) else None
    return _result_payload(result) if isinstance(result, dict) else {}


def _parallel_read_reason_label(reason: typing.Any) -> str:
    """把底层失败 reason 压缩成适合 trace 的短标签。"""
    text = str(reason or "").strip()
    if text == "file_not_found":
        return "missing"
    if text == "file_not_text":
        return "not_text"
    if text == "path_outside_workspace":
        return "outside"
    if text == "tool_not_allowed":
        return "not_allowed"
    if text == "parallel_read_item_failed":
        return "error"

    return text or "failed"


def _parallel_read_item_target(
    item: dict[str, typing.Any],
    payload: dict[str, typing.Any]
) -> str:
    """读取并行读子项最有用的目标描述。"""
    args = item.get("args") if isinstance(item.get("args"), dict) else {}
    tool = str(item.get("tool") or "").strip()

    if tool == "workspace_read_file":
        return str(payload.get("path") or args.get("path") or "").strip()
    if tool == "workspace_list_file":
        path = str(payload.get("path") or args.get("path") or ".").strip() or "."
        count = payload.get("file_count")
        return f"{path} ({count} files)" if isinstance(count, int) else path
    if tool == "workspace_search":
        query = args.get("query", payload.get("query"))
        if isinstance(query, (list, tuple)):
            target = f"{len(query)} queries"
        else:
            target = _short_text(query, 80)
        count = payload.get("match_count")
        return f"{target} ({count} matches)" if isinstance(count, int) else target
    if tool == "workspace_root":
        return str(payload.get("root") or "").strip()

    return _short_text(args, 100)


def _parallel_read_failure_summary(payload: dict[str, typing.Any]) -> str:
    """生成 native_parallel_read 标题里的失败原因摘要。"""
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
        parts.append(f"{number} {_parallel_read_reason_label(reason)}")

    return ", ".join(parts)


if __name__ == '__main__':
    pass
