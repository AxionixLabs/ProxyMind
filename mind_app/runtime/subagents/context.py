# -*- coding: utf-8 -*-

from agent.application.agents.fork_context import (
    ForkContextEntry,
    ForkContextSnapshot,
    ForkTurns,
    build_fork_context,
    normalize_fork_turns,
)
from agent.application.config.settings import DEFAULT_MAX_FORK_CONTEXT_CHARS
from mind_app.history.transcript import (
    ConversationTranscriptStore,
    TranscriptReplay,
)


def load_fork_context(
    transcript_path: str,
    fork_turns: ForkTurns,
    *,
    max_chars: int = DEFAULT_MAX_FORK_CONTEXT_CHARS,
) -> ForkContextSnapshot:
    """从历史存储读取并交给 application 构造继承上下文。"""
    normalized_fork_turns = normalize_fork_turns(fork_turns)
    if normalized_fork_turns == "none" or not str(transcript_path or "").strip():
        return build_fork_context((), normalized_fork_turns, max_chars=max_chars)

    entries = TranscriptReplay(
        ConversationTranscriptStore.reader(transcript_path).read()
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
