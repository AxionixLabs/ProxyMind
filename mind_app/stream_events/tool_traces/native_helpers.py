# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import ast
import typing
from .common import (
    _short_line,
    _short_text,
    _summary_lines
)


def _path_from_args(args: dict[str, typing.Any]) -> str:
    """从工具参数中读取路径。"""
    return str(args.get("path") or ".").strip() or "."


def _command_text(command: typing.Any) -> str:
    """把命令参数转换为单行文本。"""
    if isinstance(command, list):
        return " ".join(str(item) for item in command)
    return str(command or "").strip()


def _search_query_label(args: dict[str, typing.Any]) -> tuple[str, bool]:
    """生成搜索标题中的查询摘要，并标记是否需要引号。"""
    query = args.get("query")
    mode  = str(args.get("mode") or "").strip().lower()

    if isinstance(query, (list, tuple)):
        unit = "filenames" if mode == "file" else "queries"
        return f"{len(query)} {unit}", False

    text = str(query or "").strip()
    if text.startswith("[") and text.endswith("]"):
        try:
            parsed = ast.literal_eval(text)
        except (SyntaxError, ValueError):
            parsed = None
        if isinstance(parsed, list) and all(isinstance(item, str) for item in parsed):
            unit = "filenames" if mode == "file" else "queries"
            return f"{len(parsed)} {unit}", False

    return _short_text(text, 80), True


def _session_id_from_payload(payload: dict[str, typing.Any], args: dict[str, typing.Any]) -> str:
    """从结果载荷或参数中读取会话 ID。"""
    return str(payload.get("session_id") or args.get("session_id") or "").strip()


def _status_from_payload(payload: dict[str, typing.Any]) -> str:
    """从结果载荷中读取状态文本。"""
    raw = str(payload.get("status") or "").strip().lower()
    if raw in {"success", "failed", "cancelled", "timeout"}:
        return raw
    if payload.get("ok"):
        return "success"
    if payload.get("ok") is False:
        return "failed"

    return ""


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


def _preview_file_kind(value: typing.Any) -> str:
    """把文件类型压缩为固定宽度的预览标签。"""
    kind = str(value or "").strip().lower()
    if kind == "directory":
        return "dir"
    if kind == "symlink":
        return "link"
    if kind in {"file", "dir", "link"}:
        return kind

    return kind[:4]


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
        ("reason", data.get("reason")),
        ("error", data.get("error")),
        *pairs,
        ("hint", data.get("suggested_next_action")),
    )


def _file_action_from_args(args: dict[str, typing.Any], before_exists: typing.Any) -> str:
    """根据参数和原路径状态判断文件动作。"""
    if before_exists is False:
        return "Added"
    if before_exists is True:
        return "Edited"
    if args.get("overwrite") is False:
        return "Added"

    return "Edited"


def _unified_action(files: typing.Any) -> str:
    """根据 unified patch 文件动作集合生成摘要动作。"""
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


def _list_file_preview_lines(files: list[typing.Any]) -> list[str]:
    """把目录列表结果格式化为类型列对齐的预览行。"""
    rows: list[tuple[str, str]] = []

    for item in files:
        if not isinstance(item, dict):
            continue

        path = str(item.get("path") or "").strip()
        kind = _preview_file_kind(item.get("file_kind") or item.get("kind"))
        if not path:
            continue
        rows.append((kind, path))

    kind_width = 4 if any(kind for kind, _ in rows) else 0
    lines: list[str] = []

    for kind, path in rows:
        if kind and kind_width:
            lines.append(f"{kind:<{kind_width}} {path}")
        else:
            lines.append(path)

    return lines


if __name__ == '__main__':
    pass
