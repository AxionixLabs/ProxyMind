# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import typing
from pathlib import PurePath

_URL = re.compile(r"(?i)\b(?:https?|ftp)://[^\s'\"<>]+")

_FORCE_DELETE = {"remove-item", "ri", "rm", "del", "erase", "rd", "rmdir"}

_BROWSERS = {"chrome", "msedge", "firefox", "iexplore", "opera", "brave"}


def is_dangerous_command_windows(command: typing.Sequence[str]) -> bool:
    """判断 Windows 命令是否会强制删除或启动外部程序。"""
    words = tuple(str(item) for item in command)
    if not words:
        return False
    executable = executable_basename(words[0])
    if is_powershell_executable(executable):
        return is_dangerous_powershell(words[1:])
    if executable in {"cmd", "command"}:
        return is_dangerous_cmd(words[1:])
    return is_direct_gui_launch(words)


def is_dangerous_powershell(args: typing.Sequence[str]) -> bool:
    """判断 PowerShell 参数或命令脚本是否危险。"""
    scripts = [
        str(args[index + 1])
        for index, item in enumerate(args[:-1])
        if str(item).casefold() in {"-c", "-command", "/c"}
    ]
    text = " ".join(scripts or (str(item) for item in args))
    return is_dangerous_powershell_words(text.split())


def is_dangerous_powershell_words(words: typing.Sequence[str] | str) -> bool:
    """判断 PowerShell 词元中是否存在危险调用。"""
    text    = words if isinstance(words, str) else " ".join(str(item) for item in words)
    lowered = text.casefold()

    if has_force_delete_cmdlet(text):
        return True
    if "shell.application" in lowered or "shellexecute" in lowered:
        if args_have_url(text.split()):
            return True
        if "new-object" in lowered or "comobject" in lowered or "invoke" in lowered:
            return True
        return True
    if "rundll32" in lowered and "url.dll" in lowered:
        return True
    if any(name in lowered for name in {
        "start-process", "saps", "invoke-item", "ii", "start "
    }):
        return args_have_url(text.split())
    if any(name in lowered for name in {"mshta", "explorer", "start "}):
        return args_have_url(text.split())

    return bool(args_have_url(text.split()) and any(
        name in lowered for name in _BROWSERS
    ))


def is_dangerous_cmd(args: typing.Sequence[str]) -> bool:
    """判断 cmd /c 命令中的删除或 URL 启动操作。"""
    command_args = list(args)
    if command_args and command_args[0].casefold() in {"/c", "/k", "/q"}:
        command_args = command_args[1:]
    text = " ".join(str(item) for item in command_args)
    for segment in split_embedded_cmd_operators(text):
        words = segment.split()
        if not words:
            continue
        executable = executable_basename(words[0])
        flags = {word.casefold() for word in words[1:]}
        if executable in {"del", "erase"} and has_force_flag_cmd(flags):
            return True
        if executable in {"rd", "rmdir"} and has_recursive_flag_cmd(flags) and has_quiet_flag_cmd(flags):
            return True
        if executable == "start" and args_have_url(words[1:]):
            return True
    return False


def is_direct_gui_launch(command: typing.Sequence[str]) -> bool:
    """判断直接启动浏览器或外部 URL 的命令。"""
    executable = executable_basename(command[0])
    lowered = executable.casefold()
    if lowered in {
        "start", "start-process", "saps", "invoke-item", "ii",
        "explorer", "mshta", "rundll32",
    }:
        return args_have_url(command[1:])
    if lowered in _BROWSERS:
        return args_have_url(command[1:])
    return False


def split_embedded_cmd_operators(text: str) -> tuple[str, ...]:
    """按 cmd 常见连接符拆分嵌套命令。"""
    return tuple(part.strip() for part in re.split(r"(?:&&|\|\||[;&|])", text) if part.strip())


def has_force_delete_cmdlet(text: str) -> bool:
    """判断 PowerShell 删除 cmdlet 是否带 Force。"""
    for match in re.finditer(r"(?i)(?:^|[;&|])\s*([\w-]+)([^;&|]*)", text):
        if match.group(1).casefold() in _FORCE_DELETE and re.search(
            r"(?i)(?:^|\s)-force(?:\s|$)", match.group(2)
        ):
            return True
    return False


def has_force_flag_cmd(flags: set[str]) -> bool:
    """判断 cmd 删除命令是否带 /f。"""
    return "/f" in flags or any(flag.startswith("/f") for flag in flags)


def has_recursive_flag_cmd(flags: set[str]) -> bool:
    """判断 rd/rmdir 是否带 /s。"""
    return "/s" in flags or any(flag.startswith("/s") for flag in flags)


def has_quiet_flag_cmd(flags: set[str]) -> bool:
    """判断 rd/rmdir 是否带 /q。"""
    return "/q" in flags or any(flag.startswith("/q") for flag in flags)


def args_have_url(args: typing.Sequence[str]) -> bool:
    """判断参数中是否含有外部 URL。"""
    return any(looks_like_url(item) for item in args)


def looks_like_url(value: str) -> bool:
    """判断文本是否类似外部 URL。"""
    return bool(_URL.search(str(value or "")))


def executable_basename(value: str) -> str:
    """取得 Windows 可执行文件名。"""
    text = str(value or "").strip().strip('"\'').replace("\\", "/")
    return PurePath(text).name.casefold().removesuffix(".exe")


def is_powershell_executable(value: str) -> bool:
    """判断可执行文件是否为 PowerShell。"""
    return executable_basename(value) in {"powershell", "pwsh"}


def is_browser_executable(value: str) -> bool:
    """判断可执行文件是否为常见浏览器。"""
    return executable_basename(value) in _BROWSERS


def parse_powershell_invocation(command: typing.Sequence[str]) -> tuple[str, ...]:
    """提取 PowerShell -Command 后的脚本参数。"""
    for index, item in enumerate(command):
        if str(item).casefold() in {"-c", "-command", "/c"}:
            return tuple(str(value) for value in command[index + 1 :])
    return tuple(str(value) for value in command)


if __name__ == "__main__":
    pass
