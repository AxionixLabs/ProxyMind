# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import Mock

from metadata import const
from agent.application.views.builders.review import (
    build_review_cancelled_view,
    build_review_finished_view,
    build_review_started_view,
)
from frontends.tui.adapters.application import TuiApplicationSink
from frontends.tui.core.render import fragments_text
from frontends.tui.core.runtime import TuiRuntime
from frontends.tui.core.styles import (
    FAILURE_STYLE,
    TERMINAL_CYAN_STYLE,
    prompt_style,
)
from frontends.tui.session.title import project_session_title_update
from frontends.tui.session.turn import emit_tui_interrupt_notice
from protocol.schema.stream_events import SessionTitleUpdatedEvent


def test_review_status_title_and_interrupt_keep_codex_layout_order() -> None:
    runtime = TuiRuntime()
    application = TuiApplicationSink(runtime)
    host = SimpleNamespace(
        conversation=SimpleNamespace(update_title=Mock(return_value=True)),
        frontend=SimpleNamespace(application=application),
    )
    title_event = SessionTitleUpdatedEvent(
        type="session.title.updated",
        proto="mind.chat",
        cid="cid_review",
        sid="sid_review",
        turn_id="turn_review",
        event_seq=2,
        title="Review current changes",
    )

    application._emit_active(build_review_started_view("current changes"))
    assert project_session_title_update(host, title_event)
    application._emit_active(application.pending_views.pop())
    application._emit_active(build_review_finished_view())
    application._emit_active(build_review_cancelled_view("interrupted"))
    emit_tui_interrupt_notice(application)
    application._emit_active(application.pending_views.pop())

    blocks = runtime.document.blocks
    texts = [
        fragments_text(block.display_block.fragments)
        for block in blocks
    ]
    assert texts == [
        ">> Code review started: current changes <<",
        "• Session renamed to Review current changes. To resume this session "
        "run mind resume, then select Review current changes (sid_review)",
        "<< Code review finished >>",
        "• Review was interrupted. Please re-run /review and wait for it to "
        "complete.",
        f"■ Conversation interrupted · Tell {const.APP_DESC} what to do differently.",
    ]
    assert [block.gap_before for block in blocks] == [0, 1, 1, 1, 1]
    assert [block.kind for block in blocks] == [
        "system",
        "system",
        "system",
        "assistant",
        "notice",
    ]

    cyan_style = prompt_style(TERMINAL_CYAN_STYLE)
    assert blocks[0].display_block.fragments[0][0] == cyan_style
    assert blocks[2].display_block.fragments[0][0] == cyan_style
    assert "dim" not in blocks[0].display_block.fragments[0][0]
    assert "dim" not in blocks[2].display_block.fragments[0][0]
    assert blocks[4].display_block.fragments[0][0] == prompt_style(
        FAILURE_STYLE
    )
