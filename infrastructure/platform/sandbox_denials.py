# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import typing
from dataclasses import dataclass


_WINDOWS_POWERSHELL_RUNTIMES = frozenset({"powershell", "pwsh"})
_WINDOWS_PATH_DENIAL = re.compile(
    r"^(?P<command>Get-Content|Set-Content|Add-Content|Remove-Item|"
    r"Move-Item|Copy-Item|New-Item):\s+Access to the path '.+' is denied\.$",
    re.IGNORECASE,
)
_MACOS_SHELL_RUNTIMES = frozenset({"bash", "sh", "zsh"})
_MACOS_POLICY_DENIAL = re.compile(
    r"^(?P<program>[^:\s]+):\s+.+:\s+"
    r"(?P<reason>Operation not permitted|Permission denied|Read-only file system)$",
    re.IGNORECASE,
)
_MACOS_FILESYSTEM_PROGRAMS = frozenset({
    "cat",
    "chmod",
    "cp",
    "dd",
    "ln",
    "mkdir",
    "mv",
    "rm",
    "rmdir",
    "tee",
    "touch",
})


@dataclass(frozen=True, slots=True)
class SandboxDenialEvidence:
    """描述从已退出进程输出推断出的本地 Sandbox 拒绝证据。"""

    evidence_source: typing.Literal["inferred_output"]
    evidence_code: str
    stage: typing.Literal["execution"] = "execution"


def classify_sandbox_denial(
    *,
    backend: str,
    runtime_name: str,
    command: str,
    exit_code: int | None,
    stderr: str,
    timed_out: bool = False,
    execution_outcome_unknown: bool = False,
) -> SandboxDenialEvidence | None:
    """依据后端、退出状态和有限错误模式识别执行后的策略拒绝。"""
    if (
        isinstance(exit_code, bool)
        or not isinstance(exit_code, int)
        or exit_code == 0
        or timed_out
        or execution_outcome_unknown
    ):
        return None

    normalized_backend = str(backend or "").strip().casefold()
    normalized_runtime = str(runtime_name or "").strip().casefold()
    normalized_command = str(command or "")
    lines = tuple(line.strip() for line in str(stderr or "").splitlines())

    if (
        normalized_backend == "windows-sidecar"
        and normalized_runtime in _WINDOWS_POWERSHELL_RUNTIMES
    ):
        return _classify_windows_powershell(normalized_command, lines)
    if (
        normalized_backend == "macos-sidecar"
        and normalized_runtime in _MACOS_SHELL_RUNTIMES
    ):
        return _classify_macos_shell(normalized_command, lines)
    return None


def _classify_windows_powershell(
    command: str,
    lines: tuple[str, ...],
) -> SandboxDenialEvidence | None:
    """识别 PowerShell 文件系统命令产生的受控拒绝格式。"""
    for line in lines:
        match = _WINDOWS_PATH_DENIAL.fullmatch(line)
        if match is None:
            continue
        command_name = match.group("command")
        if not _contains_command_token(command, command_name):
            continue
        return SandboxDenialEvidence(
            evidence_source="inferred_output",
            evidence_code="windows_powershell_path_access_denied",
        )
    return None


def _classify_macos_shell(
    command: str,
    lines: tuple[str, ...],
) -> SandboxDenialEvidence | None:
    """识别 macOS Shell 文件系统命令产生的受控 Seatbelt 拒绝格式。"""
    for line in lines:
        match = _MACOS_POLICY_DENIAL.fullmatch(line)
        if match is None:
            continue
        program = match.group("program").rsplit("/", 1)[-1].casefold()
        command_matches = (
            program in _MACOS_FILESYSTEM_PROGRAMS
            and _contains_command_token(command, program)
        )
        shell_redirection = program in _MACOS_SHELL_RUNTIMES and bool(
            re.search(r"(?:^|[^<])>(?:>|&)?|<", command)
        )
        if not command_matches and not shell_redirection:
            continue
        reason = match.group("reason").casefold().replace(" ", "_")
        return SandboxDenialEvidence(
            evidence_source="inferred_output",
            evidence_code=f"macos_shell_{reason}",
        )
    return None


def _contains_command_token(command: str, token: str) -> bool:
    """判断命令文本是否包含完整的可执行命令词。"""
    return re.search(
        rf"(?<![\w-]){re.escape(token)}(?![\w-])",
        command,
        re.IGNORECASE,
    ) is not None


if __name__ == '__main__':
    pass
