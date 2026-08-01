# -*- coding: utf-8 -*-

import pytest

from mind_app.history.transcript import ConversationTranscriptStore
from mind_app.runtime.subagents.context import (
    build_fork_context,
    normalize_fork_turns,
)


def _write_turn(path, *, session_id: str, turn_id: str, user: str, assistant: str) -> None:
    writer = ConversationTranscriptStore.writer(
        path,
        session_id=session_id,
        turn_id=turn_id,
    )
    writer.open()
    writer.append("message.created", actor="user", payload={"content": user})
    writer.append(
        "message.created",
        actor="assistant",
        payload={"content": assistant},
    )
    writer.close()


def test_normalize_fork_turns_accepts_named_and_bounded_values() -> None:
    assert normalize_fork_turns(None) == "all"
    assert normalize_fork_turns(" NONE ") == "none"
    assert normalize_fork_turns("004") == "4"

    with pytest.raises(ValueError, match="fork_turns"):
        normalize_fork_turns("0")


def test_build_fork_context_selects_recent_parent_turns(tmp_path) -> None:
    path = tmp_path / "parent.jsonl"
    _write_turn(
        path,
        session_id="sid_parent",
        turn_id="turn_one",
        user="first question",
        assistant="first answer",
    )
    _write_turn(
        path,
        session_id="sid_parent",
        turn_id="turn_two",
        user="second question",
        assistant="second answer",
    )

    assert build_fork_context(str(path), "none") == ()

    recent = build_fork_context(str(path), "1")[0]
    assert "second question" in recent
    assert "first question" not in recent

    complete = build_fork_context(str(path), "all")[0]
    assert "first question" in complete
    assert "second question" in complete
