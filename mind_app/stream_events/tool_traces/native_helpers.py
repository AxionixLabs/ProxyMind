# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import typing
from .common import (
    _short_line,
    _summary_lines
)


def _patch_file_action(files: typing.Any) -> str:
    """根据 patch 结果选择文件动作文案。"""
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


def _short_sha(value: typing.Any) -> str:
    """生成短哈希文本。"""
    text = str(value or "").strip()
    return text[:12] if text else ""


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


def _error_preview_lines(data: dict[str, typing.Any], *pairs: tuple[str, typing.Any]) -> list[str]:
    """生成异常预览摘要，优先展示真实错误和少量关键字段。"""
    return _summary_lines(("error", data.get("error")), *pairs)


def failure_summary(data: dict[str, typing.Any]) -> str:
    """生成工具失败的单行摘要，供紧凑视图复用。"""
    if not isinstance(data, dict):
        return ""

    stderr = _strip_ansi(str(data.get("stderr") or "")).strip()
    if stderr:
        return _first_line(stderr)

    error = _strip_ansi(str(data.get("error") or "")).strip()
    if error:
        return error

    if data.get("timed_out"):
        return "timed out"

    exit_code = data.get("exit_code")
    if isinstance(exit_code, int):
        return f"exit code {exit_code}"

    return ""


def _first_line(value: str) -> str:
    """返回文本首个非空行。"""
    for line in str(value or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _strip_ansi(value: str) -> str:
    """移除命令输出里的 ANSI 控制序列。"""
    return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", str(value or ""))


if __name__ == '__main__':
    pass
