# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import typing
from dataclasses import dataclass
from collections.abc import Iterable
from ..config.settings import (
    DEFAULT_FORK_TURNS,
    DEFAULT_MAX_FORK_CONTEXT_CHARS,
)

ForkTurns: typing.TypeAlias = str

ForkContextRole: typing.TypeAlias = typing.Literal["user", "assistant"]


@dataclass(frozen=True, slots=True)
class ForkContextEntry:
    """保存已验证的可继承对话条目，隔离历史存储实现。"""

    turn_id: str
    role: ForkContextRole
    content: str

    def __post_init__(self) -> None:
        """校验继承条目的最小内容契约。"""
        if not isinstance(self.turn_id, str):
            raise TypeError("fork context turn id must be a string")
        if self.role not in {"user", "assistant"}:
            raise ValueError("fork context role is invalid")
        if not isinstance(self.content, str) or not self.content.strip():
            raise ValueError("fork context content is required")


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


def build_fork_context(
    entries: Iterable[ForkContextEntry],
    fork_turns: ForkTurns,
    *,
    max_chars: int = DEFAULT_MAX_FORK_CONTEXT_CHARS,
) -> ForkContextSnapshot:
    """按范围和字符预算构造父会话继承上下文。"""
    requested_turns = normalize_fork_turns(fork_turns)
    if (
        isinstance(max_chars, bool)
        or not isinstance(max_chars, int)
        or max_chars <= 0
    ):
        raise ValueError("fork context character limit must be positive")
    if requested_turns == "none":
        return ForkContextSnapshot.empty(requested_turns)

    validated_entries = tuple(entries)
    if any(not isinstance(entry, ForkContextEntry) for entry in validated_entries):
        raise TypeError("fork context entries are invalid")

    grouped = _group_turns(validated_entries)
    selected = _select_turns(grouped, requested_turns)
    if not selected:
        return ForkContextSnapshot(
            requested_turns=requested_turns,
            available_turns=len(grouped),
        )

    included: tuple[tuple[str, tuple[ForkContextEntry, ...]], ...] = ()
    rendered = ""
    for turn in reversed(selected):
        candidate = (turn, *included)
        candidate_text = _render_fork_context(candidate)
        if len(candidate_text) > max_chars:
            break
        included = candidate
        rendered = candidate_text

    return ForkContextSnapshot(
        requested_turns=requested_turns,
        parts=(rendered,) if rendered else (),
        available_turns=len(grouped),
        selected_turns=len(selected),
        included_turns=len(included),
        chars=len(rendered),
        truncated=len(included) < len(selected),
    )


def _render_fork_context(
    turns: tuple[tuple[str, tuple[ForkContextEntry, ...]], ...],
) -> str:
    """把完整轮次序列化为单个继承上下文块。"""
    payload = {
        "source": "parent_transcript",
        "turns": [
            {
                "turn_id": turn_id,
                "messages": [
                    {
                        "role": entry.role,
                        "content": entry.content,
                    }
                    for entry in turn_entries
                ],
            }
            for turn_id, turn_entries in turns
        ],
    }
    return (
        "Inherited parent conversation context:\n"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )


def _group_turns(
    entries: tuple[ForkContextEntry, ...],
) -> tuple[tuple[str, tuple[ForkContextEntry, ...]], ...]:
    """按轮次归并消息并保持首次出现顺序。"""
    grouped: list[tuple[str, list[ForkContextEntry]]] = []
    positions: dict[str, int] = {}

    for index, entry in enumerate(entries):
        turn_id = entry.turn_id.strip() or f"entry-{index}"
        position = positions.get(turn_id)
        if position is None:
            positions[turn_id] = len(grouped)
            grouped.append((turn_id, [entry]))
        else:
            grouped[position][1].append(entry)

    return tuple((turn_id, tuple(items)) for turn_id, items in grouped)


def _select_turns(
    grouped: tuple[tuple[str, tuple[ForkContextEntry, ...]], ...],
    fork_turns: ForkTurns,
) -> tuple[tuple[str, tuple[ForkContextEntry, ...]], ...]:
    """按请求范围选择父会话轮次并保持原始顺序。"""
    selected = grouped if fork_turns == "all" else grouped[-int(fork_turns):]
    return tuple(selected)


if __name__ == '__main__':
    pass
