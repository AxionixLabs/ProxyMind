# -*- coding: utf-8 -*-

from infrastructure.platform.command_safety.is_dangerous_command import (
    DangerousCommandMatch,
    dangerous_command_match,
)
from infrastructure.platform.command_safety.windows_dangerous_commands import (
    is_dangerous_command_windows,
)


def test_forced_rm_is_detected_through_shell_wrappers() -> None:
    assert dangerous_command_match(["rm", "-rf", "tmp"]) is DangerousCommandMatch.ForcedRm
    assert dangerous_command_match([
        "sudo", "env", "SAFE=1", "bash", "-lc", "echo $(rm -f tmp)"
    ]) is DangerousCommandMatch.ForcedRm
    assert dangerous_command_match([
        "sudo", "-u", "root", "rm", "--force", "tmp"
    ]) is DangerousCommandMatch.ForcedRm


def test_windows_dangerous_command_names_match_codex_cases() -> None:
    assert is_dangerous_command_windows(["cmd", "/c", "del", "/f", "tmp"])
    assert is_dangerous_command_windows([
        "powershell", "-Command", "Remove-Item -Force tmp"
    ])
    assert is_dangerous_command_windows([
        "start", "https://example.com"
    ])
