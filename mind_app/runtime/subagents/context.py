# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import typing
from agent.application.settings import DEFAULT_MAX_FORK_CONTEXT_CHARS
from agent.application.fork_context import (
    ForkContextSnapshot,
    ForkTurns,
    normalize_fork_turns,
)
from mind_app.history.transcript import (
    ConversationTranscriptStore,
    TranscriptEntry,
    TranscriptReplay
)

ForkTurnEntries: typing.TypeAlias = tuple[
    str,
    tuple[TranscriptEntry, ...],
]


def build_fork_context(
    transcript_path: str,
    fork_turns: ForkTurns,
    *,
    max_chars: int = DEFAULT_MAX_FORK_CONTEXT_CHARS,
) -> ForkContextSnapshot:
    """从父会话记录构造子会话首轮使用的结构化上下文。"""
    requested_turns = normalize_fork_turns(fork_turns)
    if (
        isinstance(max_chars, bool)
        or not isinstance(max_chars, int)
        or max_chars <= 0
    ):
        raise ValueError("fork context character limit must be positive")
    if requested_turns == "none" or not str(transcript_path or "").strip():
        return ForkContextSnapshot.empty(requested_turns)

    entries = TranscriptReplay(
        ConversationTranscriptStore.reader(transcript_path).read()
    ).build()
    messages = [
        entry
        for entry in entries
        if entry.actor in {"user", "assistant"}
        and isinstance(entry.payload.get("content"), str)
        and entry.payload["content"].strip()
    ]

    grouped  = _group_turns(messages)
    selected = _select_turns(grouped, requested_turns)

    if not selected:
        return ForkContextSnapshot(
            requested_turns=requested_turns,
            available_turns=len(grouped),
        )

    included: tuple[ForkTurnEntries, ...] = ()

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


def _render_fork_context(turns: tuple[ForkTurnEntries, ...]) -> str:
    """把完整轮次序列化为单个继承上下文块。"""

    payload = {
        "source": "parent_transcript",
        "turns": [
            {
                "turn_id": turn_id,
                "messages": [
                    {
                        "role": entry.actor,
                        "content": entry.payload["content"],
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
    entries: list[TranscriptEntry],
) -> tuple[ForkTurnEntries, ...]:
    """按轮次归并消息并保持首次出现顺序。"""
    grouped: list[tuple[str, list[TranscriptEntry]]] = []

    positions: dict[str, int] = {}

    for index, entry in enumerate(entries):
        turn_id = str(entry.turn_id or "").strip() or f"entry-{index}"

        position = positions.get(turn_id)
        if position is None:
            positions[turn_id] = len(grouped)
            grouped.append((turn_id, [entry]))
        else:
            grouped[position][1].append(entry)

    return tuple((turn_id, tuple(items)) for turn_id, items in grouped)


def _select_turns(
    grouped: tuple[ForkTurnEntries, ...],
    fork_turns: ForkTurns,
) -> tuple[ForkTurnEntries, ...]:
    """按请求范围选择父会话轮次并保持原始顺序。"""
    if fork_turns == "all":
        selected = grouped
    else:
        selected = grouped[-int(fork_turns):]

    return tuple(selected)


if __name__ == '__main__':
    pass
