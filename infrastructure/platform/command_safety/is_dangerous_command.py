# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import re
import shlex
import typing
from enum import Enum
from pathlib import PurePath

_MAX_RECURSION_DEPTH = 8
_SHELL_NAMES = {"sh", "bash", "zsh", "ksh", "dash", "fish", "ash"}
_SHELL_SWITCHES = {"-c", "-lc", "-ic", "--command", "/c"}


class DangerousCommandMatch(Enum):
    """表示危险命令的具体类别。"""

    ForcedRm = "forced_rm"
    Other = "other"


def dangerous_command_match(command: typing.Sequence[str]) -> DangerousCommandMatch | None:
    """判断命令是否包含需要拦截的危险操作。"""
    words = tuple(str(word) for word in command)
    result = dangerous_command_match_with_depth(words, depth=0)
    if result is not None:
        return result
    if os.name == "nt":
        from .windows_dangerous_commands import is_dangerous_command_windows

        if is_dangerous_command_windows(words):
            return DangerousCommandMatch.Other
    return None


def dangerous_command_match_with_depth(
    command: typing.Sequence[str],
    *,
    depth: int,
) -> DangerousCommandMatch | None:
    """递归判断包装器和 shell 脚本中的危险命令。"""
    if depth > _MAX_RECURSION_DEPTH:
        # 无法继续解析包装层时按危险处理，避免解析失败变成放行。
        return DangerousCommandMatch.Other
    words = tuple(str(word) for word in command)
    if not words:
        return None

    direct = dangerous_command_match_for_exec(words, depth=depth)
    if direct is not None:
        return direct

    executable = _executable_basename(words[0])
    if executable in _SHELL_NAMES or executable in {"cmd", "cmd.exe"}:
        for script in _shell_scripts(words[1:]):
            nested = _scan_shell_script(script, depth=depth + 1)
            if nested is not None:
                return nested
    for word in words:
        for nested_script in _nested_shell_expressions(word):
            nested = _scan_shell_script(nested_script, depth=depth + 1)
            if nested is not None:
                return nested
    return None


def dangerous_command_match_for_exec(
    command: typing.Sequence[str],
    *,
    depth: int = 0,
) -> DangerousCommandMatch | None:
    """判断一个可执行文件及其参数是否危险。"""
    if not command:
        return None
    executable = _executable_basename(command[0])
    args = tuple(command[1:])
    if executable == "rm" and rm_args_include_force_option(args):
        return DangerousCommandMatch.ForcedRm
    if executable == "sudo":
        nested = _skip_wrapper_options(args)
        return dangerous_command_match_with_depth(nested, depth=depth + 1) if nested else None
    if executable == "env":
        return dangerous_command_match_for_env(args, depth=depth + 1)
    if executable == "trap":
        return dangerous_command_match_for_trap(args, depth=depth + 1)
    return None


def dangerous_command_match_for_env(
    args: typing.Sequence[str],
    *,
    depth: int,
) -> DangerousCommandMatch | None:
    """判断 env 包装的实际命令。"""
    remaining = list(args)
    options_with_value = {"-C", "--chdir", "-u", "--unset", "-S", "--split-string"}
    while remaining:
        value = str(remaining[0])
        if "=" in value and not value.startswith("--"):
            remaining.pop(0)
            continue
        if value in options_with_value:
            remaining.pop(0)
            if remaining:
                remaining.pop(0)
            continue
        if value.startswith("-"):
            remaining.pop(0)
            continue
        break
    return (
        dangerous_command_match_with_depth(remaining, depth=depth)
        if remaining
        else None
    )


def dangerous_command_match_for_trap(
    args: typing.Sequence[str],
    *,
    depth: int,
) -> DangerousCommandMatch | None:
    """判断 trap 动作字符串中嵌套的命令。"""
    if not args:
        return None
    action = str(args[0])
    return _scan_shell_script(action, depth=depth)


def rm_args_include_force_option(args: typing.Sequence[str]) -> bool:
    """判断 rm 参数是否包含强制删除选项。"""
    for arg in args:
        value = str(arg)
        if value == "--":
            break
        if value == "--force" or value == "-f":
            return True
        if value.startswith("-") and not value.startswith("--") and "f" in value[1:]:
            return True
    return False


def dangerous_powershell_words_match(command: typing.Sequence[str]) -> DangerousCommandMatch | None:
    """判断 PowerShell 词元是否包含 Windows 危险命令。"""
    from .windows_dangerous_commands import is_dangerous_powershell_words

    return (
        DangerousCommandMatch.Other
        if is_dangerous_powershell_words(command)
        else None
    )


def _scan_shell_script(script: str, *, depth: int) -> DangerousCommandMatch | None:
    if depth > _MAX_RECURSION_DEPTH:
        return DangerousCommandMatch.Other
    text = str(script or "")
    # 先直接识别脚本中的 rm，覆盖条件分支、命令替换和重定向。
    try:
        words = shlex.split(text, posix=True)
    except ValueError:
        words = re.findall(r"[^\s;&|()]+", text)
    for index, word in enumerate(words):
        if _executable_basename(word) == "rm":
            candidate = [word, *words[index + 1:]]
            if rm_args_include_force_option(candidate[1:]):
                return DangerousCommandMatch.ForcedRm
        nested = dangerous_command_match_with_depth(words[index:], depth=depth)
        if nested is not None:
            return nested
    return None


def _shell_scripts(args: typing.Sequence[str]) -> tuple[str, ...]:
    scripts: list[str] = []
    for index, arg in enumerate(args):
        if str(arg).casefold() in _SHELL_SWITCHES and index + 1 < len(args):
            scripts.append(str(args[index + 1]))
    return tuple(scripts)


def _nested_shell_expressions(word: str) -> tuple[str, ...]:
    values = re.findall(r"\$\(([^()]*)\)|`([^`]*)`", str(word))
    return tuple(left or right for left, right in values)


def _skip_wrapper_options(args: typing.Sequence[str]) -> tuple[str, ...]:
    remaining = list(args)
    options_with_value = {
        "-u", "--user", "-g", "--group", "-h", "--host",
        "-p", "--prompt", "-C", "--chdir", "-R", "--chroot",
    }
    while remaining:
        value = str(remaining[0])
        if value == "--":
            remaining.pop(0)
            break
        if value in options_with_value:
            remaining.pop(0)
            if remaining:
                remaining.pop(0)
            continue
        if value.startswith("-"):
            remaining.pop(0)
            continue
        break
    return tuple(remaining)


def _executable_basename(value: str) -> str:
    text = str(value or "").strip().strip('"\'')
    text = text.replace("\\", "/")
    return PurePath(text).name.casefold().removesuffix(".exe")


if __name__ == '__main__':
    pass
