# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class InlineCommandPreview(object):
    """命令展示信息，包含标题和可选的内联脚本预览内容。"""
    title: str
    script: str = ""
    path: str = "inline.txt"

    @property
    def has_script(self) -> bool:
        """是否包含可单独展示的内联脚本。"""
        return bool(self.script)


def command_text(command: typing.Any) -> str:
    """把命令字段转换为单行展示文本。"""
    if isinstance(command, list):
        return " ".join(str(item) for item in command)

    return str(command or "").strip()


def command_preview(command: typing.Any) -> InlineCommandPreview:
    """生成命令展示信息；多行参数会拆出预览内容。"""
    if isinstance(command, list):
        return _list_command_preview(command)

    return _string_command_preview(command)


def inline_script_preview_lines(script: str, *, path: str = "inline.py") -> list[str]:
    """把内联脚本文本格式化为路径行和带行号的代码预览。"""
    lines = str(
        script or ""
    ).replace("\r\n", "\n").replace("\r", "\n").split("\n")

    while lines and not lines[-1]:
        lines.pop()

    if not lines:
        return []

    return [
        path,
        *[
            f"{index:>4}  {line}"
            for index, line in enumerate(lines, start=1)
        ]
    ]


def _list_command_preview(command: list[typing.Any]) -> InlineCommandPreview:
    """处理数组形式命令的展示信息。"""
    parts = [str(item) for item in command]
    if parts:
        parts[0] = _display_command_head(parts[0])

    script_index = _first_multiline_arg_index(parts)
    if script_index is None:
        return InlineCommandPreview(title=command_text(parts))

    script = parts[script_index]
    title_parts = [
        *parts[:script_index],
        "<inline script>",
        *parts[script_index + 1:]
    ]

    return InlineCommandPreview(
        title=command_text(title_parts),
        script=script,
        path=_inline_path_for_command(parts)
    )


def _string_command_preview(command: typing.Any) -> InlineCommandPreview:
    """处理字符串形式命令的展示信息。"""
    text = str(command or "").strip()
    if "\n" not in text and "\r" not in text:
        return InlineCommandPreview(title=_display_command_text(text))

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")

    first_break = normalized.find("\n")
    if first_break < 0:
        return InlineCommandPreview(title=text)

    head = normalized[:first_break].rstrip()
    tail = normalized[first_break + 1:].lstrip()

    if not head or not tail:
        return InlineCommandPreview(title=text)

    return InlineCommandPreview(
        title=f"{_display_command_text(head)} <inline script>",
        script=tail,
        path=_inline_path_for_command([head])
    )


def _first_multiline_arg_index(parts: list[str]) -> int | None:
    """返回第一个多行参数的位置。"""
    for index, part in enumerate(parts):
        if "\n" in part or "\r" in part:
            return index

    return None


def _normalize_executable(value: str) -> str:
    """归一化命令可执行文件名。"""
    executable = str(
        value or ""
    ).replace("\\", "/").rsplit("/", 1)[-1].lower()

    return executable.rsplit(".", 1)[0]


def _display_command_head(value: str) -> str:
    """把内置工具的绝对路径压缩为稳定展示名。"""
    text = str(value or "").strip()
    if _normalize_executable(text) == "rg":
        return "rg"

    return text


def _display_command_text(value: str) -> str:
    """压缩字符串命令中的命令头展示。"""
    text = str(value or "").strip()
    if not text:
        return text

    if text[0] in {"'", "\""}:
        quote = text[0]
        end = text.find(quote, 1)
        if end > 0:
            head = text[1:end]
            if _normalize_executable(head) == "rg":
                return "rg" + text[end + 1:]
        return text

    parts = text.split(maxsplit=1)
    if parts and _normalize_executable(parts[0]) == "rg":
        return "rg" + (f" {parts[1]}" if len(parts) > 1 else "")

    return text


def _inline_path_for_command(parts: list[str]) -> str:
    """根据命令名返回预览文件名。"""
    executable = _normalize_executable(parts[0]) if parts else ""

    suffix = {
        "python"     : "py",
        "python3"    : "py",
        "py"         : "py",
        "node"       : "js",
        "nodejs"     : "js",
        "bash"       : "sh",
        "sh"         : "sh",
        "zsh"        : "sh",
        "powershell" : "ps1",
        "pwsh"       : "ps1",
    }.get(executable, "txt")

    return f"inline.{suffix}"


if __name__ == '__main__':
    pass
