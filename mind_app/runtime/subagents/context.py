# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import typing
from mind_app.history.transcript import (
    ConversationTranscriptStore,
    TranscriptEntry,
    TranscriptReplay
)

ForkTurns: typing.TypeAlias = str


def normalize_fork_turns(value: typing.Any) -> ForkTurns:
    """规范化子会话初始上下文继承范围。"""
    if value is None:
        return "all"
    if not isinstance(value, str):
        raise TypeError("fork_turns must be a string")

    normalized = value.strip().casefold()
    if normalized in {"none", "all"}:
        return normalized
    if normalized.isdigit() and int(normalized) > 0:
        return str(int(normalized))

    raise ValueError("fork_turns must be none, all, or a positive integer")


def build_fork_context(
    transcript_path: str,
    fork_turns: ForkTurns,
) -> tuple[str, ...]:
    """从父会话记录构造子会话首轮使用的结构化上下文。"""
    if fork_turns == "none" or not str(transcript_path or "").strip():
        return ()

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
    selected = _select_turns(messages, fork_turns)
    if not selected:
        return ()

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
            for turn_id, turn_entries in selected
        ],
    }
    return (
        "Inherited parent conversation context:\n"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    )


def _select_turns(
    entries: list[TranscriptEntry],
    fork_turns: ForkTurns,
) -> tuple[tuple[str, tuple[TranscriptEntry, ...]], ...]:
    """按继承范围选择父会话轮次并保持原始顺序。"""
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

    if fork_turns == "all":
        selected = grouped
    else:
        selected = grouped[-int(fork_turns):]

    return tuple((turn_id, tuple(items)) for turn_id, items in selected)


if __name__ == '__main__':
    pass
