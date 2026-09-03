# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from .is_dangerous_command import (
    DangerousCommandMatch,
    dangerous_command_match,
    dangerous_powershell_words_match,
)
from .windows_dangerous_commands import is_dangerous_command_windows

__all__ = [
    "DangerousCommandMatch",
    "dangerous_command_match",
    "dangerous_powershell_words_match",
    "is_dangerous_command_windows",
]
