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


def _shell_call_item_label(
    item: dict[str, typing.Any],
    payload: dict[str, typing.Any]
) -> str:
    """生成 shell call 子项的简洁预览行。"""
    args = item.get("args") if isinstance(item.get("args"), dict) else {}
    tool = str(item.get("tool") or "").strip()

    if tool == "shell_command":
        command = args.get("command", payload.get("command"))
        return _short_text(command, 100)

    target = _short_text(args, 100)
    label  = tool or "item"
    return f"{label} {target}".strip()


if __name__ == '__main__':
    pass
