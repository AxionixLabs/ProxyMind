# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CommandPreview(object):
    """命令展示信息。"""
    title: str


def command_text(command: typing.Any) -> str:
    """把命令字段转换为单行展示文本。"""
    if isinstance(command, list):
        return " ".join(str(item) for item in command)

    return str(command or "").strip()


def command_preview(command: typing.Any) -> CommandPreview:
    """生成命令展示信息。"""
    if isinstance(command, list):
        return _list_command_preview(command)

    return _string_command_preview(command)


def _list_command_preview(command: list[typing.Any]) -> CommandPreview:
    """处理数组形式命令的展示信息。"""
    parts = [str(item) for item in command]
    parts = _display_command_parts(parts)
    if parts:
        parts[0] = _display_command_head(parts[0])
    return CommandPreview(title=command_text(parts))


def _string_command_preview(command: typing.Any) -> CommandPreview:
    """处理字符串形式命令的展示信息。"""
    text = str(command or "").strip()
    return CommandPreview(title=_display_command_text(text))


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

    text = _hide_shell_wrapper_text(text)

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


def _display_command_parts(parts: list[str]) -> list[str]:
    """压缩列表命令中的展示前缀。"""
    stripped = _hide_shell_wrapper_parts(parts)
    return stripped if stripped is not None else parts


def _hide_shell_wrapper_parts(parts: list[str]) -> list[str] | None:
    """隐藏列表命令中的 shell 包装前缀。"""
    if not parts:
        return None

    executable = _normalize_executable(parts[0])
    marker_index = _shell_marker_index(executable, parts[1:])
    if marker_index is None:
        return None

    command_index = marker_index + 2
    if command_index >= len(parts):
        return None

    command = str(parts[command_index] or "").strip()
    if not command:
        return None

    return [command, *parts[command_index + 1:]]


def _hide_shell_wrapper_text(text: str) -> str:
    """隐藏字符串命令中的 shell 包装前缀。"""
    match = _shell_marker_match(text)
    if match is None:
        return text

    marker_index, marker = match
    command = text[marker_index + len(marker):].strip()
    if not command:
        return text

    return _strip_matching_quotes(command)


def _shell_marker_index(
    executable: str,
    args: list[str]
) -> int | None:
    """返回 shell 命令内容标记在参数列表中的位置。"""
    markers = _shell_markers(executable)
    if not markers:
        return None

    for index, arg in enumerate(args):
        if str(arg or "").strip().lower() in markers:
            return index

    return None


def _shell_marker_match(text: str) -> tuple[int, str] | None:
    """查找字符串命令中可隐藏的 shell marker。"""
    candidates = [
        ("-command", {"powershell", "pwsh"}),
        ("-lc", {"bash", "sh", "zsh"}),
        ("-c", {"bash", "powershell", "pwsh", "sh", "zsh"}),
        ("/c", {"cmd"}),
    ]
    found: tuple[int, str] | None = None

    for marker, shells in candidates:
        index = _find_argument_marker(text, marker)
        if index < 0:
            continue
        if not _prefix_contains_shell(text[:index], shells):
            continue
        if found is None or index < found[0]:
            found = (index, marker)

    return found


def _shell_markers(executable: str) -> set[str]:
    """返回 shell 的命令内容参数。"""
    if executable in {"powershell", "pwsh"}:
        return {"-command", "-c"}
    if executable in {"bash", "sh", "zsh"}:
        return {"-c", "-lc"}
    if executable == "cmd":
        return {"/c"}

    return set()


def _find_argument_marker(text: str, marker: str) -> int:
    """按参数边界查找 marker。"""
    lower  = text.lower()
    marker = marker.lower()
    start  = 0

    while True:
        index = lower.find(marker, start)
        if index < 0:
            return -1

        before_ok = index == 0 or lower[index - 1].isspace()
        after     = index + len(marker)
        after_ok  = after >= len(lower) or lower[after].isspace()

        if before_ok and after_ok:
            return index

        start = index + 1


def _prefix_contains_shell(prefix: str, shells: set[str]) -> bool:
    """判断 marker 前缀里是否包含目标 shell。"""
    normalized = str(prefix or "").replace("\\", "/").lower()
    for chunk in normalized.replace("/", " ").split():
        executable = _normalize_executable(chunk.strip("'\""))
        if executable in shells:
            return True

    return False


def _strip_matching_quotes(text: str) -> str:
    """去掉命令内容外层的一组对称引号。"""
    value = str(text or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", "\""}:
        return value[1:-1]

    return value


if __name__ == '__main__':
    pass
