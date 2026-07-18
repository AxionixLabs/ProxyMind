import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from prompt_toolkit.application import create_app_session
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.cursor_shapes import CursorShape
from prompt_toolkit.data_structures import Point
from prompt_toolkit.document import Document
from prompt_toolkit.input import DummyInput
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType

from mind_tui.app import TuiApp
from mind_tui.approval import ApprovalOverlay, _normalize_decisions
from mind_tui.bottom_pane import BottomPaneState
from mind_tui.commands import CommandCompleter
from mind_tui.dispatch import AppEventDispatcher
from mind_tui.events import AppEvent
from mind_tui.projector import AppEventProjector, ProjectionActions
from mind_tui.runtime import LiveStreamProvider, TurnRequest
from mind_tui.runtime import live as live_runtime
from mind_tui.runtime import tools as tool_runtime_module
from mind_tui.runtime.tools import TuiToolRuntime
from mind_tui.scrollbar import calculate_scrollbar_geometry
from mind_tui.state import TuiState, consume_shell_prefix
from mind_tui.style import TUI_STYLE
from mind_tui.transcript import TranscriptCell, TranscriptLexer, TranscriptRenderer
from mind_core.prompting.commands import SlashCommandCompleter
from mind_app.client_tools.result import client_tool_result


class StubStreamProvider:
    """提供正式应用边界测试使用的事件流。"""

    def __init__(self) -> None:
        self.messages: list[tuple[str, bool]] = []

    def stream(self, request: TurnRequest) -> AsyncIterator[AppEvent]:
        self.messages.append((request.message, request.shell_mode))

        async def events() -> AsyncIterator[AppEvent]:
            yield AppEvent("turn.start")
            yield AppEvent("text.delta", "provider reply")
            yield AppEvent("text.done")
            yield AppEvent("turn.done")

        return events()


class IdleStreamProvider:
    """提供不产生业务内容的界面测试运行时。"""

    def stream(self, request: TurnRequest) -> AsyncIterator[AppEvent]:
        async def events() -> AsyncIterator[AppEvent]:
            yield AppEvent("turn.start")
            yield AppEvent("turn.done")

        return events()


class ApprovalStreamProvider:
    """提供队列和审批交互测试使用的事件流。"""

    def __init__(self, delay: float) -> None:
        self.delay = delay

    def stream(self, request: TurnRequest) -> AsyncIterator[AppEvent]:
        async def events() -> AsyncIterator[AppEvent]:
            yield AppEvent("turn.start")
            yield AppEvent("turn.thinking")
            await asyncio.sleep(self.delay)
            reply = asyncio.get_running_loop().create_future()
            yield AppEvent(
                "tool.approval_required",
                payload={
                    "id": "approval-1",
                    "title": "Review command",
                    "tool": "rg",
                    "command": "rg -n stream mind_tui",
                    "availableDecisions": ["accept", "decline"]
                },
                reply=reply
            )
            await reply
            yield AppEvent(
                "tool.call",
                payload={
                    "name": "rg",
                    "arguments": {"command": "rg -n stream mind_tui"}
                }
            )
            yield AppEvent(
                "tool.output",
                payload={"name": "rg", "output": "Completed"}
            )
            yield AppEvent("turn.done")

        return events()


class WaitingStreamProvider:
    """提供首个服务端事件到达前的等待状态。"""

    def __init__(self) -> None:
        self.release = asyncio.Event()

    def stream(self, request: TurnRequest) -> AsyncIterator[AppEvent]:
        async def events() -> AsyncIterator[AppEvent]:
            await self.release.wait()
            yield AppEvent("turn.done")

        return events()


def create_tui(
    stream_provider: object | None = None,
    **kwargs: object
) -> TuiApp:
    """创建注入测试运行时的终端应用。"""
    return TuiApp(
        stream_provider=stream_provider or IdleStreamProvider(),
        **kwargs
    )


def test_shell_prefix_is_consumed_once() -> None:
    assert consume_shell_prefix("!pytest", 7, shell_mode=False) == ("pytest", 6, True)
    assert consume_shell_prefix("pytest!", 7, shell_mode=False) == ("pytest!", 7, False)
    assert consume_shell_prefix("!again", 6, shell_mode=True) == ("!again", 6, True)


def test_status_slot_can_be_hidden_without_losing_state() -> None:
    state = TuiState("header")
    state.status.set("Thinking")
    state.status.tick()
    assert state.status.text == "Thinking"
    assert state.status.phase == 1
    state.status.set("")
    assert state.status.text == ""


def test_message_queue_uses_fifo_and_rolls_back_latest_item() -> None:
    state = TuiState("header")
    state.turn.enqueue("first")
    state.turn.enqueue("second", shell_mode=True)
    rolled_back = state.turn.rollback()
    assert rolled_back is not None
    assert rolled_back.text == "second"
    assert rolled_back.shell_mode is True
    next_item = state.turn.dequeue()
    assert next_item is not None
    assert next_item.text == "first"
    assert state.turn.queued_messages == []


def test_bottom_pane_routes_input_to_the_top_view() -> None:
    pane = BottomPaneState()
    pane.push("approval", {"id": "approval-1"})
    pane.push("settings")
    assert not pane.composer_visible
    assert pane.top is not None
    assert pane.top.kind == "settings"
    pane.pop("settings")
    assert pane.is_active("approval")
    pane.pop("approval")
    assert pane.composer_visible


def test_app_event_dispatcher_serializes_background_events() -> None:
    async def scenario() -> None:
        handled: list[str] = []

        async def handle(event: AppEvent) -> None:
            await asyncio.sleep(0)
            handled.append(event.kind)

        dispatcher = AppEventDispatcher(handle)
        dispatcher.start()
        first = asyncio.create_task(dispatcher.dispatch(AppEvent("turn.start")))
        await asyncio.sleep(0)
        second = asyncio.create_task(dispatcher.dispatch(AppEvent("turn.done")))
        await asyncio.gather(first, second)
        assert handled == ["turn.start", "turn.done"]
        assert dispatcher.pending_count == 0
        await dispatcher.stop()

    asyncio.run(scenario())


def test_app_event_projector_runs_without_terminal_layout() -> None:
    async def scenario() -> None:
        statuses: list[str] = []
        stream: list[str] = []
        blocks: list[tuple[str, str]] = []

        async def approve(payload: dict) -> str:
            assert payload["id"] == "approval-1"
            return "accept"

        projector = AppEventProjector(
            ProjectionActions(
                set_status=statuses.append,
                write_stream=stream.append,
                finish_stream=lambda: stream.append("<done>"),
                write_block=lambda text, kind: blocks.append((kind, text)),
                request_approval=approve
            )
        )
        await projector.project(AppEvent("turn.thinking"))
        await projector.project(AppEvent("text.delta", "reply"))
        reply = asyncio.get_running_loop().create_future()
        await projector.project(AppEvent(
            "tool.approval_required",
            payload={"id": "approval-1"},
            reply=reply
        ))

        assert statuses == ["Working", ""]
        assert stream == ["reply"]
        assert blocks == [("approval", "• Approval approved")]
        assert reply.result() == "accept"

    asyncio.run(scenario())


def test_transcript_uses_one_global_blank_line_between_content_blocks() -> None:
    state = TuiState(">_ Mind")
    state.transcript.write_block("\n› hello\n\n", kind="user")
    state.transcript.write_stream("\nassistant ")
    state.transcript.write_stream("reply\n")
    state.transcript.finish_stream()
    state.transcript.write_block("\n\n• Called rg\n  └ command\n\n")
    rendered = state.transcript.render()
    assert rendered == (
        ">_ Mind\n\n"
        "› hello\n\n"
        "assistant reply\n\n"
        "• Called rg\n"
        "  └ command"
    )
    assert "\n\n\n" not in rendered
    assert [cell.kind for cell in state.transcript.cells] == [
        "header",
        "user",
        "assistant",
        "trace"
    ]


def test_markdown_is_rendered_when_stream_lands() -> None:
    state = TuiState("")
    markdown = (
        "# Heading\n\n"
        "A **bold** and *italic* value with `inline` code.\n\n"
        "- first\n"
        "- second\n\n"
        "```python\n"
        "print('ok')\n"
        "```"
    )
    state.transcript.write_stream(markdown)
    renderer = TranscriptRenderer()

    streaming = renderer.render(state.transcript.cells)
    assert "# Heading" in streaming.text
    assert "**bold**" in streaming.text

    assert state.transcript.finish_stream()
    landed = renderer.render(state.transcript.cells)
    assert "# Heading" not in landed.text
    assert "**bold**" not in landed.text
    assert "Heading" in landed.text
    assert "A bold and italic value with inline code." in landed.text
    assert "• first" in landed.text
    assert "python\n  print('ok')" in landed.text

    styles = {
        style
        for line in landed.lines
        for style, _ in line
    }
    assert "class:markdown.heading.1" in styles
    assert any("class:markdown.strong" in style for style in styles)
    assert "class:markdown.code.inline" in styles
    assert "class:markdown.code.block" in styles


def test_transcript_lexer_preserves_selectable_plain_text() -> None:
    document = Document(">_ Mind\n› hello\n• Explored\n  └ file.py")
    lexer = TranscriptLexer().lex_document(document)
    assert lexer(0) == [("class:header", ">_ Mind")]
    assert lexer(1) == [("class:user", "› hello")]
    assert lexer(3) == [("class:trace", "  └ file.py")]


def test_header_only_highlights_brand_name() -> None:
    rendered = TranscriptRenderer().render((
        TranscriptCell("header", ">_ Mind (v1.1.7)"),
    ))
    assert rendered.lines[0] == (
        ("class:header.dim", ">_ "),
        ("class:header.brand", "Mind"),
        ("class:header.dim", " (v1.1.7)"),
    )


def test_empty_transcript_area_does_not_fill_available_height() -> None:
    with create_app_session(input=DummyInput(), output=DummyOutput()):
        tui = create_tui()
    assert tui.output_window.dont_extend_height()
    assert tui.input_window.dont_extend_height()
    transcript = tui.state.transcript.render()
    assert not transcript.endswith("\n")
    assert "Tip:" not in transcript
    assert tui.input_control.focus_on_click()
    assert not tui.input_window.always_hide_cursor()
    assert tui.application.cursor.get_cursor_shape(tui.application) == CursorShape.BEAM
    assert tui.application.refresh_interval is None
    assert not tui.application.full_screen


def test_footer_stays_one_row_below_chinese_input() -> None:
    async def scenario() -> None:
        with create_pipe_input() as pipe:
            output = DummyOutput()
            with create_app_session(input=pipe, output=output):
                tui = create_tui()
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
                    assert render_info.window_height == 1
                    assert wrapped_prompt_row == initial_prompt_row
                    assert wrapped_footer_row == initial_footer_row
                    assert wrapped_footer_row == (
                        wrapped_prompt_row + render_info.window_height + 1
                    )
                finally:
                    tui.application.exit(result=None)
                    await asyncio.wait_for(task, timeout=2)

    asyncio.run(scenario())


def test_status_animation_keeps_blank_row_above_input() -> None:
    async def scenario() -> None:
        with create_pipe_input() as pipe:
            with create_app_session(input=pipe, output=DummyOutput()):
                tui = create_tui(ApprovalStreamProvider(0.5))
                task = asyncio.create_task(tui.run())
                try:
                    await asyncio.sleep(0.05)
                    pipe.send_text("status spacing\r")
                    await asyncio.sleep(0.05)
                    screen = tui.application.renderer._last_screen
                    status_row = next(
                        row
                        for row, columns in screen.data_buffer.items()
                        if "Working" in "".join(
                            columns[column].char for column in sorted(columns)
                        )
                    )
                    user_row = _screen_row(tui, "› status spacing")
                    assert status_row == user_row + 2
                    assert _prompt_row(tui) == status_row + 2
                finally:
                    tui.application.exit(result=None)
                    await asyncio.wait_for(task, timeout=2)

    asyncio.run(scenario())


def test_query_starts_working_before_first_runtime_event() -> None:
    async def scenario() -> None:
        provider = WaitingStreamProvider()
        with create_pipe_input() as pipe:
            with create_app_session(input=pipe, output=DummyOutput()):
                tui = create_tui(provider)
                task = asyncio.create_task(tui.run())
                try:
                    await asyncio.sleep(0.05)
                    pipe.send_text("start immediately\r")
                    await asyncio.sleep(0.05)
                    assert tui.state.status.text == "Working"
                    assert "Working" in _screen_text(tui)
                    provider.release.set()
                finally:
                    tui.application.exit(result=None)
                    await asyncio.wait_for(task, timeout=2)

    asyncio.run(scenario())


def test_mouse_click_restores_input_focus() -> None:
    async def scenario() -> None:
        with create_pipe_input() as pipe:
            with create_app_session(input=pipe, output=DummyOutput()):
                tui = create_tui()
                task = asyncio.create_task(tui.run())
                try:
                    await asyncio.sleep(0.05)
                    tui.application.layout.focus(tui.output_control)
                    tui.input_control.mouse_handler(
                        MouseEvent(
                            position=Point(x=0, y=0),
                            event_type=MouseEventType.MOUSE_UP,
                            button=MouseButton.LEFT,
                            modifiers=frozenset()
                        )
                    )
                    assert tui.application.layout.current_control is tui.input_control
                    pipe.send_text("click input")
                    await asyncio.sleep(0.05)
                    assert tui.input_buffer.text == "click input"
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
                tui = create_tui()
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
                tui = create_tui(ApprovalStreamProvider(0.5))
                task = asyncio.create_task(tui.run())
                try:
                    await asyncio.sleep(0.05)
                    pipe.send_text("first turn\r")
                    await asyncio.sleep(0.05)
                    assert tui.state.turn.busy

                    pipe.send_text("下一步做什么？")
                    await asyncio.sleep(0.05)
                    assert "tab to queue message" in _screen_text(tui)
                    pipe.send_text("\t")
                    await asyncio.sleep(0.05)
                    assert [item.text for item in tui.state.turn.queued_messages] == [
                        "下一步做什么？"
                    ]
                    assert "Messages to be submitted" in _screen_text(tui)

                    pipe.send_text("Write tests for @filename")
                    pipe.send_text("\t")
                    await asyncio.sleep(0.05)
                    assert len(tui.state.turn.queued_messages) == 2

                    pipe.send_text("\x1b[1;5D")
                    await asyncio.sleep(0.05)
                    assert tui.input_buffer.text == "Write tests for @filename"
                    assert len(tui.state.turn.queued_messages) == 1

                    pipe.send_text("\t")
                    await asyncio.sleep(0.05)
                    pipe.send_text("\x1b")
                    await asyncio.sleep(0.2)
                    assert tui.state.turn.submitted[:2] == [
                        "first turn",
                        "下一步做什么？"
                    ]
                    assert [item.text for item in tui.state.turn.queued_messages] == [
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
                tui = create_tui(ApprovalStreamProvider(0.1))
                task = asyncio.create_task(tui.run())
                try:
                    await asyncio.sleep(0.05)
                    pipe.send_text("first turn\r")
                    await asyncio.sleep(0.03)
                    pipe.send_text("queued turn\t")
                    for _ in range(100):
                        if tui.approval.visible:
                            break
                        await asyncio.sleep(0.02)
                    assert tui.approval.visible
                    pipe.send_text("y")
                    for _ in range(100):
                        if len(tui.state.turn.submitted) >= 2:
                            break
                        await asyncio.sleep(0.02)
                    assert tui.state.turn.submitted[:2] == [
                        "first turn",
                        "queued turn"
                    ]
                    assert tui.state.turn.queued_messages == []
                finally:
                    tui.application.exit(result=None)
                    await asyncio.wait_for(task, timeout=2)

    asyncio.run(scenario())


def test_approval_automatically_pauses_and_resumes_full_stream_chain() -> None:
    async def scenario() -> None:
        with create_pipe_input() as pipe:
            with create_app_session(input=pipe, output=DummyOutput()):
                tui = create_tui(ApprovalStreamProvider(0.01))
                task = asyncio.create_task(tui.run())
                try:
                    await asyncio.sleep(0.05)
                    pipe.send_text("automatic approval\r")
                    for _ in range(100):
                        if tui.approval.visible:
                            break
                        await asyncio.sleep(0.02)
                    assert tui.approval.visible
                    assert not tui.composer_row.filter()
                    assert tui.application.layout.current_control is tui.approval.control

                    pipe.send_text("y")
                    for _ in range(50):
                        if "Called rg" in tui.state.transcript.render():
                            break
                        await asyncio.sleep(0.02)
                    assert not tui.approval.visible
                    assert tui.composer_row.filter()
                    assert tui.application.layout.current_control is tui.input_control
                    assert "Approval approved" in tui.state.transcript.render()
                    assert "Called rg" in tui.state.transcript.render()
                finally:
                    tui.application.exit(result=None)
                    await asyncio.wait_for(task, timeout=2)

    asyncio.run(scenario())


def test_approval_card_keeps_one_blank_row_before_footer() -> None:
    async def scenario() -> None:
        with create_pipe_input() as pipe:
            with create_app_session(input=pipe, output=DummyOutput()):
                tui = create_tui(ApprovalStreamProvider(0.01))
                task = asyncio.create_task(tui.run())
                try:
                    await asyncio.sleep(0.05)
                    pipe.send_text("approval spacing\r")
                    for _ in range(100):
                        if tui.approval.visible:
                            break
                        await asyncio.sleep(0.02)
                    assert tui.approval.visible
                    await asyncio.sleep(0.05)
                    assert _footer_row(tui) == _approval_last_option_row(tui) + 2
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
    scrollbar_styles = {
        name: style
        for name, style in TUI_STYLE.style_rules
        if name.startswith("scrollbar")
    }
    assert all("bg:" not in style for style in scrollbar_styles.values())


def test_approval_card_keeps_supported_decision_order() -> None:
    approval = {
        "availableDecisions": ["decline", "acceptForSession", "unknown", "accept"]
    }
    assert _normalize_decisions(approval) == ["decline", "acceptForSession", "accept"]
    card = ApprovalOverlay()
    card.approval = {"title": "Review", "command": "pytest"}
    rendered = "".join(text for _, text in card._render())
    assert "Would you like to run the following command?" in rendered
    assert "$ pytest" in rendered
    assert not any(character in rendered for character in "┌┐└┘─│")
    approval_styles = dict(TUI_STYLE.style_rules)
    assert "bg:#D5D9DE" in approval_styles["approval.card"]


def test_live_stream_provider_maps_remote_events(monkeypatch) -> None:
    class FakeReport:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def open(self) -> None:
            pass

        def begin_turn(self) -> str:
            return "turn-1"

        def bind_event(self, event: dict) -> None:
            pass

        async def flush(self) -> None:
            pass

        async def close(self) -> None:
            pass

    class FakeRuntime:
        report = object()

        def __init__(self) -> None:
            self.last_reply = ""

        async def fresh_pref_config(self, *, ttl_sec: float) -> dict:
            return {"primary": {}}

        def begin_session(self, **kwargs: object) -> dict[str, str]:
            return {"cid": "cid-1", "sid": "sid-1"}

        async def with_mcp_session(self, pref_config: dict, function) -> None:
            await function(object(), [])

        def is_service_mcp_linked(self) -> bool:
            return False

        def service_exec_env_snapshot(self) -> None:
            return None

        def remember_last_assistant_reply(self, text: str) -> None:
            self.last_reply = text

    def stream_chat(*args: object, **kwargs: object) -> AsyncIterator[dict]:
        async def events() -> AsyncIterator[dict]:
            yield {"type": "turn.start"}
            yield {"type": "turn.thinking"}
            yield {"type": "text.delta", "text": "real reply"}
            yield {"type": "text.done"}
            yield {"type": "turn.done"}

        return events()

    monkeypatch.setattr(live_runtime, "EventReport", FakeReport)
    monkeypatch.setattr(live_runtime.request, "stream_chat", stream_chat)

    async def collect() -> tuple[list[str], FakeRuntime]:
        runtime = FakeRuntime()
        provider = LiveStreamProvider(runtime)
        kinds = [
            event.kind
            async for event in provider.stream(TurnRequest("hello"))
        ]
        return kinds, runtime

    kinds, runtime = asyncio.run(collect())
    assert kinds == [
        "turn.start",
        "turn.thinking",
        "text.delta",
        "text.done",
        "turn.done"
    ]
    assert runtime.last_reply == "real reply"


def test_tui_tool_runtime_returns_approval_and_posts_tool_result(monkeypatch) -> None:
    posted_approvals: list[dict[str, object]] = []
    posted_results: list[dict[str, object]] = []
    emitted: list[AppEvent] = []

    async def post_approval(
        cid: str,
        sid: str,
        call_id: str,
        approval_id: str,
        decision: str,
        reason: str | None = None,
        timeout: float = 60.0
    ) -> None:
        posted_approvals.append({
            "cid": cid,
            "sid": sid,
            "call_id": call_id,
            "approval_id": approval_id,
            "decision": decision
        })

    async def post_result(
        cid: str,
        sid: str,
        call_id: str,
        name: str,
        ok: bool,
        result: object,
        execution: dict | None = None
    ) -> dict:
        posted_results.append({
            "cid": cid,
            "sid": sid,
            "call_id": call_id,
            "name": name,
            "ok": ok,
            "result": result
        })
        return {"ok": True}

    class FakeSession:
        async def call_tool(
            self,
            name: str,
            arguments: dict,
            **kwargs: object
        ):
            return client_tool_result(
                tool=name,
                ok=True,
                text="tool completed",
                args=arguments,
                data={"value": 1}
            )

    class FakeRuntime:
        report = object()

    async def emit(event: AppEvent) -> None:
        emitted.append(event)
        if event.reply is not None:
            event.reply.set_result("accept")

    monkeypatch.setattr(
        tool_runtime_module.request,
        "post_tool_approval",
        post_approval
    )
    monkeypatch.setattr(
        tool_runtime_module.request,
        "post_tool_result",
        post_result
    )

    async def scenario() -> None:
        runtime = TuiToolRuntime(
            mind=FakeRuntime(),
            session=FakeSession(),
            tools=[{"name": "demo_tool", "meta": {}}],
            pref_config={"primary": {}},
            emit=emit
        )
        await runtime.handle_approval({
            "cid": "cid-1",
            "sid": "sid-1",
            "call_id": "call-approval",
            "approval": {
                "id": "approval-1",
                "tool": "demo_tool",
                "arguments": {"value": 1}
            }
        })
        outcome = await runtime.execute_call({
            "type": "tool.call",
            "cid": "cid-1",
            "sid": "sid-1",
            "call_id": "call-tool",
            "name": "demo_tool",
            "arguments": {"value": 1}
        })
        assert outcome is not None
        assert outcome.ok
        assert outcome.text.endswith("tool completed")

    asyncio.run(scenario())
    assert emitted[0].kind == "tool.approval_required"
    assert posted_approvals[0]["decision"] == "accept"
    assert posted_results[0]["name"] == "demo_tool"
    assert posted_results[0]["ok"] is True


def test_application_consumes_injected_stream_provider() -> None:
    async def scenario() -> None:
        provider = StubStreamProvider()
        with create_pipe_input() as pipe:
            with create_app_session(input=pipe, output=DummyOutput()):
                tui = create_tui(provider)
                task = asyncio.create_task(tui.run())
                try:
                    await asyncio.sleep(0.05)
                    pipe.send_text("provider request\r")
                    for _ in range(50):
                        if not tui.state.turn.busy and provider.messages:
                            break
                        await asyncio.sleep(0.02)
                    assert provider.messages == [("provider request", False)]
                    assert "provider reply" in tui.state.transcript.render()
                finally:
                    tui.application.exit(result=None)
                    await asyncio.wait_for(task, timeout=2)

    asyncio.run(scenario())


def test_module_entry_runs_application(monkeypatch) -> None:
    from mind_tui import __main__ as entry

    calls: list[tuple[str, object]] = []

    class FakeRuntime:
        history_workspace = "workspace"

        async def fresh_pref_config(self, *, ttl_sec: float) -> dict:
            return {"primary": {"model": "test-model"}}

    @asynccontextmanager
    async def fake_open_runtime():
        yield FakeRuntime()

    def fake_provider(runtime: object) -> object:
        calls.append(("provider", runtime))
        return "live-provider"

    class FakeApp:
        def __init__(self, **kwargs: object) -> None:
            calls.append(("app", kwargs))

        async def run(self) -> None:
            calls.append(("run", None))

    monkeypatch.setattr(entry, "open_runtime", fake_open_runtime)
    monkeypatch.setattr(entry, "LiveStreamProvider", fake_provider)
    monkeypatch.setattr(entry, "TuiApp", FakeApp)
    assert asyncio.run(entry.run()) == 0
    assert calls[0][0] == "provider"
    assert calls[1] == (
        "app",
        {
            "stream_provider": "live-provider",
            "model_label": "test-model",
            "workspace_label": "workspace"
        }
    )
    assert calls[2] == ("run", None)


def test_legacy_main_remains_isolated_from_tui() -> None:
    source = Path("mind.py").read_text(encoding="utf-8")
    assert "mind_tui" not in source


def test_tui_does_not_import_legacy_stream_module() -> None:
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path("mind_tui").rglob("*.py")
    )
    assert "mind_app.modes.stream" not in sources
    assert "SimulatedStreamProvider" not in sources


def _footer_row(tui: TuiApp) -> int:
    """返回测试屏幕中信息栏所在行。"""
    screen = tui.application.renderer._last_screen
    for row, columns in screen.data_buffer.items():
        text = "".join(columns[column].char for column in sorted(columns))
        if "gpt-5.6-sol high" in text:
            return row
    raise AssertionError("footer is not visible")


def _approval_last_option_row(tui: TuiApp) -> int:
    """返回测试屏幕中审批卡最后一个选择所在行。"""
    screen = tui.application.renderer._last_screen
    for row, columns in screen.data_buffer.items():
        text = "".join(columns[column].char for column in sorted(columns))
        if "No, and tell Mind what to do differently" in text:
            return row
    raise AssertionError("approval option is not visible")


def _prompt_row(tui: TuiApp) -> int:
    """返回测试屏幕中输入提示符所在行。"""
    screen = tui.application.renderer._last_screen
    rows: list[int] = []
    for row, columns in screen.data_buffer.items():
        text = "".join(columns[column].char for column in sorted(columns))
        if text.startswith("›"):
            rows.append(row)
    if rows:
        return max(rows)
    raise AssertionError("input prompt is not visible")


def _screen_text(tui: TuiApp) -> str:
    """返回测试屏幕中的可见文本。"""
    screen = tui.application.renderer._last_screen
    lines = [
        "".join(columns[column].char for column in sorted(columns)).rstrip()
        for columns in screen.data_buffer.values()
    ]
    return "\n".join(lines)


def _screen_row(tui: TuiApp, expected: str) -> int:
    """返回测试屏幕中包含指定文本的行号。"""
    screen = tui.application.renderer._last_screen
    for row, columns in screen.data_buffer.items():
        text = "".join(columns[column].char for column in sorted(columns))
        if expected in text:
            return row
    raise AssertionError(f"screen text is not visible: {expected}")
