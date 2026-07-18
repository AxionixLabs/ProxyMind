import asyncio

from prompt_toolkit.application import create_app_session
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document
from prompt_toolkit.input import DummyInput
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from mind_tui.app import TranscriptLexer, TuiApp
from mind_tui.approval import ApprovalOverlay, _normalize_decisions
from mind_tui.commands import CommandCompleter
from mind_tui.events import simulate_stream
from mind_tui.scrollbar import calculate_scrollbar_geometry
from mind_tui.state import TuiState, consume_shell_prefix
from mind_tui.style import TUI_STYLE
from mind_core.prompting.commands import SlashCommandCompleter


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


def test_message_queue_uses_fifo_and_rolls_back_latest_item() -> None:
    state = TuiState("header")
    state.enqueue("first")
    state.enqueue("second", shell_mode=True)
    rolled_back = state.rollback_queue()
    assert rolled_back is not None
    assert rolled_back.text == "second"
    assert rolled_back.shell_mode is True
    next_item = state.dequeue()
    assert next_item is not None
    assert next_item.text == "first"
    assert state.queued_messages == []


def test_transcript_lexer_preserves_selectable_plain_text() -> None:
    document = Document(">_ Mind\n› hello\n• Explored\n  └ file.py")
    lexer = TranscriptLexer().lex_document(document)
    assert lexer(0) == [("class:header", ">_ Mind")]
    assert lexer(1) == [("class:user", "› hello")]
    assert lexer(3) == [("class:trace", "  └ file.py")]


def test_empty_transcript_area_does_not_fill_available_height() -> None:
    with create_app_session(input=DummyInput(), output=DummyOutput()):
        tui = TuiApp()
    assert tui.output_window.dont_extend_height()
    assert tui.input_window.dont_extend_height()
    assert not tui.state.transcript.endswith("\n")


def test_footer_stays_one_row_below_chinese_input() -> None:
    async def scenario() -> None:
        with create_pipe_input() as pipe:
            output = DummyOutput()
            with create_app_session(input=pipe, output=output):
                tui = TuiApp()
                task = asyncio.create_task(tui.run())
                try:
                    await asyncio.sleep(0.05)
                    initial_prompt_row = _prompt_row(tui)
                    initial_footer_row = _footer_row(tui)
                    assert initial_footer_row == initial_prompt_row + 2

                    pipe.send_text("中文输入" * 30)
                    await asyncio.sleep(0.1)
                    wrapped_prompt_row = _prompt_row(tui)
                    wrapped_footer_row = _footer_row(tui)
                    render_info = tui.input_window.render_info
                    assert render_info is not None
                    assert wrapped_footer_row == (
                        wrapped_prompt_row + render_info.window_height + 1
                    )
                finally:
                    tui.application.exit(result=None)
                    await asyncio.wait_for(task, timeout=2)

    asyncio.run(scenario())


def test_command_menu_matches_existing_top_level_commands() -> None:
    completer = CommandCompleter()
    completions = list(completer.get_completions(Document("/"), CompleteEvent()))
    assert [item.display_text for item in completions] == list(
        SlashCommandCompleter.TOP_LEVEL
    )
    skills = next(item for item in completions if item.display_text == "/skills")
    assert skills.text == "/skills"


def test_command_menu_renders_as_borderless_indented_list() -> None:
    async def scenario() -> None:
        with create_pipe_input() as pipe:
            with create_app_session(input=pipe, output=DummyOutput()):
                tui = TuiApp()
                task = asyncio.create_task(tui.run())
                try:
                    await asyncio.sleep(0.05)
                    pipe.send_text("/")
                    await asyncio.sleep(0.1)
                    screen = tui.application.renderer._last_screen
                    lines = [
                        "".join(columns[column].char for column in sorted(columns)).rstrip()
                        for columns in screen.data_buffer.values()
                    ]
                    chat_line = next(line for line in lines if "/chat" in line)
                    assert chat_line.startswith("  /chat")
                    assert not any(
                        character in chat_line
                        for character in "┌┐└┘│"
                    )
                finally:
                    tui.application.exit(result=None)
                    await asyncio.wait_for(task, timeout=2)

    asyncio.run(scenario())


def test_busy_composer_can_queue_rollback_and_interrupt() -> None:
    async def scenario() -> None:
        with create_pipe_input() as pipe:
            with create_app_session(input=pipe, output=DummyOutput()):
                tui = TuiApp(stream_delay=0.5)
                task = asyncio.create_task(tui.run())
                try:
                    await asyncio.sleep(0.05)
                    pipe.send_text("first turn\r")
                    await asyncio.sleep(0.05)
                    assert tui.state.busy

                    pipe.send_text("下一步做什么？")
                    await asyncio.sleep(0.05)
                    assert "tab to queue message" in _screen_text(tui)
                    pipe.send_text("\t")
                    await asyncio.sleep(0.05)
                    assert [item.text for item in tui.state.queued_messages] == [
                        "下一步做什么？"
                    ]
                    assert "Messages to be submitted" in _screen_text(tui)

                    pipe.send_text("Write tests for @filename")
                    pipe.send_text("\t")
                    await asyncio.sleep(0.05)
                    assert len(tui.state.queued_messages) == 2

                    pipe.send_text("\x1b[1;5D")
                    await asyncio.sleep(0.05)
                    assert tui.input_buffer.text == "Write tests for @filename"
                    assert len(tui.state.queued_messages) == 1

                    pipe.send_text("\t")
                    await asyncio.sleep(0.05)
                    pipe.send_text("\x1b")
                    await asyncio.sleep(0.2)
                    assert tui.state.submitted[:2] == [
                        "first turn",
                        "下一步做什么？"
                    ]
                    assert [item.text for item in tui.state.queued_messages] == [
                        "Write tests for @filename"
                    ]
                finally:
                    tui.application.exit(result=None)
                    await asyncio.wait_for(task, timeout=2)

    asyncio.run(scenario())


def test_queued_message_starts_after_next_tool_event() -> None:
    async def scenario() -> None:
        with create_pipe_input() as pipe:
            with create_app_session(input=pipe, output=DummyOutput()):
                tui = TuiApp(stream_delay=0.05)
                task = asyncio.create_task(tui.run())
                try:
                    await asyncio.sleep(0.05)
                    pipe.send_text("first turn\r")
                    await asyncio.sleep(0.03)
                    pipe.send_text("queued turn\t")
                    await asyncio.sleep(0.3)
                    assert tui.state.submitted[:2] == [
                        "first turn",
                        "queued turn"
                    ]
                    assert tui.state.queued_messages == []
                finally:
                    tui.application.exit(result=None)
                    await asyncio.wait_for(task, timeout=2)

    asyncio.run(scenario())


def test_custom_scrollbar_uses_glyphs_instead_of_background_colors() -> None:
    geometry = calculate_scrollbar_geometry(
        content_height=100,
        viewport_height=20,
        track_height=40,
        scroll_offset=80
    )
    assert geometry is not None
    assert geometry.thumb_top + geometry.thumb_height == geometry.track_height
    assert geometry.max_scroll == 80
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


def _footer_row(tui: TuiApp) -> int:
    """返回测试屏幕中信息栏所在行。"""
    screen = tui.application.renderer._last_screen
    for row, columns in screen.data_buffer.items():
        text = "".join(columns[column].char for column in sorted(columns))
        if "gpt-5.6-sol high" in text:
            return row
    raise AssertionError("footer is not visible")


def _prompt_row(tui: TuiApp) -> int:
    """返回测试屏幕中输入提示符所在行。"""
    screen = tui.application.renderer._last_screen
    for row, columns in screen.data_buffer.items():
        text = "".join(columns[column].char for column in sorted(columns))
        if text.startswith("›"):
            return row
    raise AssertionError("input prompt is not visible")


def _screen_text(tui: TuiApp) -> str:
    """返回测试屏幕中的可见文本。"""
    screen = tui.application.renderer._last_screen
    lines = [
        "".join(columns[column].char for column in sorted(columns)).rstrip()
        for columns in screen.data_buffer.values()
    ]
    return "\n".join(lines)
