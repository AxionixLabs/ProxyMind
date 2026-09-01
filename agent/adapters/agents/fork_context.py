# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from agent.application.agents.fork_context import (
    ForkContextEntry,
    ForkContextSnapshot,
    ForkTurns,
    build_fork_context,
    normalize_fork_turns,
)
from agent.application.config.settings import DEFAULT_MAX_FORK_CONTEXT_CHARS
from agent.stores.transcripts import (
    TranscriptEntry,
    TranscriptReplay,
)

TranscriptEntriesReader = typing.Callable[
    [str],
    typing.Iterable[TranscriptEntry],
]


def load_fork_context(
    transcript_path: str,
    fork_turns: ForkTurns,
    *,
    transcript_entries_for: TranscriptEntriesReader | None = None,
    max_chars: int = DEFAULT_MAX_FORK_CONTEXT_CHARS,
) -> ForkContextSnapshot:
    """从注入的 Transcript 读取器读取并构造继承上下文。"""
    normalized_fork_turns = normalize_fork_turns(fork_turns)
    if normalized_fork_turns == "none" or not str(transcript_path or "").strip():
        return build_fork_context((), normalized_fork_turns, max_chars=max_chars)
    if transcript_entries_for is None:
        raise ValueError(
            "transcript_entries_for is required when transcript_path is set"
        )

    entries = TranscriptReplay(
        transcript_entries_for(transcript_path)
    ).build()

    return build_fork_context(
        tuple(
            ForkContextEntry(
                turn_id=str(entry.turn_id or ""),
                role=entry.actor,
                content=entry.payload["content"],
            )
            for entry in entries
            if entry.actor in {"user", "assistant"}
            and isinstance(entry.payload.get("content"), str)
            and entry.payload["content"].strip()
        ),
        normalized_fork_turns,
        max_chars=max_chars,
    )


if __name__ == '__main__':
    pass
