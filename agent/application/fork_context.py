# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass
from .settings import DEFAULT_FORK_TURNS

ForkTurns: typing.TypeAlias = str


@dataclass(frozen=True, slots=True)
class ForkContextSnapshot:
    """保存继承上下文及其范围和预算统计。"""
    requested_turns: ForkTurns
    parts: tuple[str, ...] = ()
    available_turns: int = 0
    selected_turns: int = 0
    included_turns: int = 0
    chars: int = 0
    truncated: bool = False

    def __post_init__(self) -> None:
        """校验上下文内容和统计字段的一致性。"""
        requested_turns = normalize_fork_turns(self.requested_turns)
        if not isinstance(self.parts, tuple) or any(
            not isinstance(part, str) or not part
            for part in self.parts
        ):
            raise TypeError("fork context parts must be non-empty strings")
        counts = (
            self.available_turns,
            self.selected_turns,
            self.included_turns,
            self.chars,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in counts
        ):
            raise ValueError("fork context statistics must be non-negative integers")
        if not (
            self.included_turns <= self.selected_turns <= self.available_turns
        ):
            raise ValueError("fork context turn statistics are inconsistent")
        if self.chars != sum(len(part) for part in self.parts):
            raise ValueError("fork context character count is inconsistent")
        if bool(self.parts) != bool(self.included_turns):
            raise ValueError("fork context content and included turns do not match")
        if not isinstance(self.truncated, bool):
            raise TypeError("fork context truncated flag must be a boolean")
        object.__setattr__(self, "requested_turns", requested_turns)

    @classmethod
    def empty(cls, requested_turns: ForkTurns) -> "ForkContextSnapshot":
        """创建不包含上下文内容的快照。"""
        return cls(requested_turns=requested_turns)


def normalize_fork_turns(
    value: typing.Any,
    *,
    default_turns: int = DEFAULT_FORK_TURNS,
) -> ForkTurns:
    """规范化子会话初始上下文继承范围。"""
    if (
        isinstance(default_turns, bool)
        or not isinstance(default_turns, int)
        or default_turns <= 0
    ):
        raise ValueError("default fork turns must be a positive integer")
    if value is None:
        return str(default_turns)
    if not isinstance(value, str):
        raise TypeError("fork_turns must be a string")

    normalized = value.strip().casefold()
    if normalized in {"none", "all"}:
        return normalized
    if normalized.isdigit() and int(normalized) > 0:
        return str(int(normalized))

    raise ValueError("fork_turns must be none, all, or a positive integer")


if __name__ == '__main__':
    pass
