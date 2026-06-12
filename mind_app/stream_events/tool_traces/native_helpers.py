# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from .common import (
    _short_line,
    _summary_lines
)


def _path_from_args(args: dict[str, typing.Any]) -> str:
    """从工具参数中读取路径。"""
    return str(args.get("path") or ".").strip() or "."


def _file_action_from_before_exists(before_exists: typing.Any) -> str:
    """根据写入前的文件状态选择文件动作文案。"""
    if before_exists is False:
        return "Added"
    if before_exists is True:
        return "Edited"

    return "Edited"


def _unified_file_action(files: typing.Any) -> str:
    """根据 unified patch 结果选择文件动作文案。"""
    if not isinstance(files, list):
        return "Edited"
    if len(files) != 1 or not isinstance(files[0], dict):
        return "Edited"

    action = str(files[0].get("action") or "").strip().lower()
    if action in {"create", "add"}:
        return "Added"
    if action == "delete":
        return "Deleted"

    return "Edited"


def _format_delta(added: int, removed: int) -> str:
    """格式化新增和删除行数摘要。"""
    if added <= 0 and removed <= 0:
        return ""
    return f" (+{max(0, added)} -{max(0, removed)})"


def _failure_suffix(payload: dict[str, typing.Any], *, ok: bool) -> str:
    """生成失败标题后缀，并优先带上失败原因。"""
    if ok:
        return ""
    reason = str(payload.get("reason") or "").strip()
    return f" failed: {reason}" if reason else " failed"


def _short_sha(value: typing.Any) -> str:
    """生成短哈希文本。"""
    text = str(value or "").strip()
    return text[:12] if text else ""


def _format_size(value: typing.Any) -> str:
    """格式化字节大小。"""
    try:
        size = int(value)
    except (TypeError, ValueError):
        return ""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"

    return f"{size / (1024 * 1024):.1f} MB"


def _diagnostic_sequence_lines(label: str, value: typing.Any) -> list[str]:
    """把失败诊断中的 expected/actual 序列压缩成单行显示。"""
    if isinstance(value, list):
        text = " | ".join(str(item) for item in value[:3])
        if len(value) > 3:
            text = f"{text} | ..."
    else:
        text = str(value or "")

    text = _short_line(text, 100)
    return [f"{label}: {text}"] if text else []


def _failure_preview_lines(data: dict[str, typing.Any], *pairs: tuple[str, typing.Any]) -> list[str]:
    """生成失败预览摘要，优先展示 reason 和少量关键字段。"""
    return _summary_lines(
        ("reason", data.get("reason")), ("error", data.get("error")), *pairs
    )


if __name__ == '__main__':
    pass
