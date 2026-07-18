import asyncio
from types import SimpleNamespace

from prompt_toolkit.document import Document

from mind_app.tui.app import TranscriptLexer
from mind_app.tui.approval import ApprovalOverlay, _normalize_decisions
from mind_app.tui.events import simulate_stream
from mind_app.tui.scrollbar import ScrollbarMargin
from mind_app.tui.state import TuiState, consume_shell_prefix
from mind_app.tui.style import TUI_STYLE


def test_shell_prefix_is_consumed_once() -> None:
    assert consume_shell_prefix("!pytest", 7, shell_mode=False) == ("pytest", 6, True)
    assert consume_shell_prefix("pytest!", 7, shell_mode=False) == ("pytest!", 7, False)
    assert consume_shell_prefix("!again", 6, shell_mode=True) == ("!again", 6, True)


def test_status_slot_can_be_hidden_without_losing_state() -> None:
    state = TuiState("header")
    state.set_status("Thinking")
    state.tick_status()
    assert state.status_text == "Thinking"
    assert state.status_phase == 1
    state.set_status("")
    assert state.status_text == ""


def test_transcript_lexer_preserves_selectable_plain_text() -> None:
    document = Document(">_ Mind\n› hello\n• Explored\n  └ file.py")
    lexer = TranscriptLexer().lex_document(document)
    assert lexer(0) == [("class:header", ">_ Mind")]
    assert lexer(1) == [("class:user", "› hello")]
    assert lexer(3) == [("class:trace", "  └ file.py")]


def test_custom_scrollbar_uses_glyphs_instead_of_background_colors() -> None:
    info = SimpleNamespace(
        content_height=100,
        window_height=20,
        displayed_lines=list(range(20)),
        vertical_scroll=40
    )
    fragments = ScrollbarMargin().create_margin(info, width=1, height=20)
    assert len(fragments) == 20
    assert any(text == "┃\n" for _, text in fragments)
    assert all("bg:" not in style for style, _ in fragments)
    assert "bg:" not in str(TUI_STYLE.style_rules)


def test_approval_card_keeps_supported_decision_order() -> None:
    approval = {
        "availableDecisions": ["decline", "acceptForSession", "unknown", "accept"]
    }
    assert _normalize_decisions(approval) == ["decline", "acceptForSession", "accept"]
    card = ApprovalOverlay()
    card.approval = {"title": "Review", "command": "pytest"}
    rendered = "".join(text for _, text in card._render())
    assert "Review" in rendered
    assert "$ pytest" in rendered


def test_simulated_stream_matches_stream_event_shape() -> None:
    async def collect() -> list[str]:
        return [event.kind async for event in simulate_stream("hello", delay=0)]

    kinds = asyncio.run(collect())
    assert kinds[0] == "status"
    assert "text.block" in kinds
    assert "text.delta" in kinds
    assert kinds[-1] == "done"
