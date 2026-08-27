# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from prompt_toolkit.utils import get_cwidth

from mind_app.subscription.forwarding import AgentInbox
from mind_app.subscription.models import AgentForwardRequest
from mind_app.tui.adapters.application import TuiApplicationSink
from mind_app.tui.core.mailbox import (
    TuiMailboxOverlay,
    format_mailbox_count,
)
from mind_app.tui.core.models import (
    MailboxEntry,
    MenuDescriptionLayout,
    STANDARD_MENU_FOOTER_HINT,
)
from mind_app.tui.core.interrupt import InterruptDisposition
from mind_app.tui.core.render import fragments_text
from mind_app.tui.core.runtime import TuiRuntime
from mind_app.tui.features.mailbox import TuiMailboxFeature
from mind_app.tui.features.listener import (
    choose_listener_action,
    parse_listener_command,
    render_listener_failure,
    render_listener_interrupted,
    render_listener_result,
    render_listener_status,
)
from mind_app.tui.session.barriers import TuiForegroundTasks


class _Listener(object):
    def __init__(self, *requests: AgentForwardRequest) -> None:
        self.inbox = AgentInbox()
        for request in requests:
            self.inbox.add(request)
        self.callback = None
        self.running = True
        self.receipt_disposition_resolver = None

    def is_running(self) -> bool:
        return self.running

    def bind_inbox_changed(self, callback) -> None:
        self.callback = callback
        if callback is not None:
            callback()

    def bind_receipt_disposition(self, resolver) -> None:
        self.receipt_disposition_resolver = resolver


class _OperationListener(object):
    def __init__(self, *, running: bool, ready: bool) -> None:
        self.inbox = AgentInbox()
        self.running = running
        self.ready = ready
        self.wait_started = asyncio.Event()
        self.ready_signal = asyncio.Event()

    def is_running(self) -> bool:
        return self.running

    def is_ready(self) -> bool:
        return self.running and self.ready

    async def wait_until_ready(self) -> None:
        self.wait_started.set()
        await self.ready_signal.wait()
        self.ready = True


class _OperationController(object):
    def __init__(
        self,
        runtime: TuiRuntime,
        listener: _OperationListener,
    ) -> None:
        sink = TuiApplicationSink(runtime)
        self.frontend = SimpleNamespace(
            runtime=runtime,
            application=SimpleNamespace(emit=sink._emit_active),
        )
        self.subscription_runtime = listener
        self.animate = True
        self.pause_started = asyncio.Event()
        self.pause_release = asyncio.Event()

    @staticmethod
    def await_cleanup(awaitable):
        return awaitable

    def start_subscription_listener(self) -> _OperationListener:
        self.subscription_runtime.running = True
        return self.subscription_runtime

    async def pause_subscription_listener(self) -> None:
        self.pause_started.set()
        await self.pause_release.wait()
        self.subscription_runtime.running = False
        self.subscription_runtime.ready = False


def _request(
    message_id: str,
    message: str,
    *,
    summary: str = "",
) -> AgentForwardRequest:
    payload = {"message": message}
    if summary:
        payload["intent"] = {"summary": summary}
    return AgentForwardRequest(
        message_id=message_id,
        call_id=f"call-{message_id}",
        cid="cid-1",
        sid="sid-1",
        payload=payload,
    )


def _entry(index: int, message: str = "message") -> MailboxEntry:
    return MailboxEntry(
        str(index),
        f"Title {index}",
        message,
        f"call {index}",
    )


def _text(parts) -> str:
    return "".join(value for _style, value in parts)


def test_listener_messages_update_existing_footer_without_adding_height() -> None:
    runtime = TuiRuntime()
    listener = _Listener(_request("1", "inspect workspace", summary="Inspect"))
    controller = SimpleNamespace(subscription_runtime=listener)

    initial_height = runtime.screen._footer_height()
    TuiMailboxFeature(runtime, controller).bind_listener()

    assert runtime.screen._footer_height() == initial_height == 1
    assert "Mailbox 1" in _text(runtime.screen._footer_fragments())

    runtime.screen.invalidate = Mock()
    listener.running = False
    listener.callback()

    runtime.screen.invalidate.assert_not_called()

    listener.inbox.add(_request("2", "run tests"))
    listener.callback()

    assert runtime.screen._footer_height() == 1
    assert "Mailbox 2" in _text(runtime.screen._footer_fragments())
    assert runtime.screen.mailbox_overlay.pending_count == 2


@pytest.mark.anyio
async def test_mailbox_detail_reuses_single_application_and_returns_to_menu() -> None:
    runtime = TuiRuntime()
    application = runtime.screen.application
    runtime.set_mailbox_entries((_entry(1, "inspect workspace"),), listener_active=True)

    viewing = asyncio.create_task(runtime.view_mailbox_entry("1"))
    await asyncio.sleep(0)

    assert runtime.screen.application is application
    assert runtime.screen.mailbox_overlay.active
    assert application.full_screen
    assert not runtime.screen.transcript_overlay.active
    assert "inspect workspace" in _text(
        runtime.screen.mailbox_overlay.visible_fragments()
    )

    runtime.close_mailbox_overlay()

    assert await viewing
    assert not runtime.screen.mailbox_overlay.active
    assert not application.full_screen
    assert runtime.screen._inline_renderer_state is None


@pytest.mark.anyio
async def test_mailbox_detail_wraps_wide_messages_and_preserves_scroll() -> None:
    invalidations = []
    overlay = TuiMailboxOverlay(
        get_width=lambda: 24,
        get_height=lambda: 12,
        invalidate=lambda: invalidations.append(True),
    )
    overlay.update(
        (
            _entry(1, "x" * 60),
            MailboxEntry("2", "中文标题", "界" * 120, "call two"),
        ),
        listener_active=True,
    )
    assert overlay.open("2")

    text = _text(overlay.visible_fragments())
    assert overlay.selected_entry is not None
    assert overlay.selected_entry.key == "2"
    assert "中文标题" in text
    assert "call two" in text
    assert all(get_cwidth(line) <= 24 for line in text.splitlines())

    overlay.scroll_page(1)
    assert overlay.message_offset > 0
    assert overlay.message_progress()[0] == 2

    previous_offset = overlay.message_offset
    overlay.update(
        (
            _entry(1, "x" * 60),
            MailboxEntry("2", "中文标题", "界" * 120, "call two"),
            _entry(3, "new"),
        ),
        listener_active=True,
    )

    assert overlay.selected_entry is not None
    assert overlay.selected_entry.key == "2"
    assert overlay.message_offset == previous_offset
    assert invalidations

    overlay.close()
    await overlay.wait_closed()


@pytest.mark.parametrize(
    ("value", "expected"),
    (
        ("/listen", (True, None)),
        (" /LISTEN START ", (True, "start")),
        ("/listen stop", (True, "stop")),
        ("/listen status", (True, "status")),
        ("/listen restart", (False, None)),
        ("/listener start", (False, None)),
    ),
)
def test_parse_listener_command(value: str, expected) -> None:
    assert parse_listener_command(value) == expected


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("running", "ready", "expected_selected"),
    (
        (False, False, 0),
        (True, False, 1),
        (True, True, 1),
    ),
)
async def test_listener_menu_uses_command_description(
    running: bool,
    ready: bool,
    expected_selected: int,
) -> None:
    runtime = SimpleNamespace(select_menu=AsyncMock(return_value="start"))
    listener = _OperationListener(running=running, ready=ready)
    controller = SimpleNamespace(subscription_runtime=listener)

    selected = await choose_listener_action(runtime, controller)

    assert selected == "start"
    request = runtime.select_menu.await_args.args[0]
    assert request.title == "Update Listener"
    assert request.title_accent_suffix == ""
    assert request.status == "Start or stop the remote request listener."
    assert request.body == ()
    assert request.selected == expected_selected
    assert request.view_id == "listener:root"
    assert request.help_text == ""
    assert request.footer_hint == STANDARD_MENU_FOOTER_HINT
    assert (
        request.description_layout
        is MenuDescriptionLayout.STACK_BELOW_WHEN_NARROW
    )
    assert [option.label for option in request.options] == [
        "Start listener",
        "Stop listener",
    ]


@pytest.mark.parametrize(
    ("outcome", "expected"),
    (
        ("ready", "■ Listener ready"),
        ("already_ready", "■ Listener already ready"),
        ("stopped", "■ Listener stopped"),
        ("already_stopped", "■ Listener already stopped"),
    ),
)
def test_listener_result_is_a_compact_stable_block(
    outcome: str,
    expected: str,
) -> None:
    views = []
    controller = SimpleNamespace(
        frontend=SimpleNamespace(
            application=SimpleNamespace(emit=views.append),
        ),
    )

    render_listener_result(controller, outcome)

    assert [view.type for view in views] == [
        "tui.listener.status",
        "tui.gap",
    ]
    assert views[0].renderable.plain_text == expected


@pytest.mark.parametrize(
    ("running", "ready", "pending", "expected"),
    (
        (
            False,
            False,
            0,
            "/listen status\n\n"
            "Listener\n\n"
            "  • Status: stopped · Pending: 0",
        ),
        (
            True,
            False,
            1,
            "/listen status\n\n"
            "Listener\n\n"
            "  • Status: connecting · Pending: 1",
        ),
        (
            True,
            True,
            2,
            "/listen status\n\n"
            "Listener\n\n"
            "  • Status: listening · Pending: 2",
        ),
    ),
)
def test_listener_status_uses_query_layout_instead_of_stop_result_style(
    running: bool,
    ready: bool,
    pending: int,
    expected: str,
) -> None:
    views = []
    listener = (
        _OperationListener(running=running, ready=ready)
        if running or ready or pending
        else None
    )
    if listener is not None:
        for index in range(pending):
            listener.inbox.add(_request(str(index), f"message {index}"))
    controller = SimpleNamespace(
        subscription_runtime=listener,
        frontend=SimpleNamespace(
            application=SimpleNamespace(emit=views.append),
        ),
    )

    render_listener_status(controller)

    assert [view.type for view in views] == [
        "tui.listener.status",
        "tui.gap",
    ]
    text = fragments_text(views[0].renderable.fragments)
    assert text == expected
    assert not text.startswith("■")


def test_listener_failure_and_interruption_do_not_echo_command() -> None:
    views = []
    controller = SimpleNamespace(
        frontend=SimpleNamespace(
            application=SimpleNamespace(emit=views.append),
        ),
    )

    render_listener_failure(
        controller,
        "start",
        RuntimeError("connection failed"),
    )
    failure_text = views[0].renderable.plain_text
    views.clear()

    render_listener_interrupted(controller, "start")
    interrupted_text = fragments_text(views[0].renderable.fragments)

    assert failure_text == (
        "■ Listener failed\n"
        "  └ RuntimeError: connection failed"
    )
    assert interrupted_text == "• Listener · start interrupted"
    assert "/listen" not in failure_text
    assert "/listen" not in interrupted_text


@pytest.mark.anyio
async def test_listener_start_hands_spinner_directly_to_ready_result() -> None:
    runtime = TuiRuntime()
    listener = _OperationListener(running=False, ready=False)
    controller = _OperationController(runtime, listener)
    foreground = TuiForegroundTasks(runtime, controller)

    foreground.start_listener("start")
    waiting = asyncio.create_task(foreground.wait())
    await listener.wait_started.wait()

    assert runtime.screen.activity_block is not None
    assert "Listener starting" in fragments_text(
        runtime.screen.activity_block.fragments,
    )
    assert "/listen start" not in fragments_text(
        runtime.document.fragments(width=80),
    )

    listener.ready_signal.set()
    await waiting

    transcript = fragments_text(runtime.document.fragments(width=80))
    assert runtime.screen.activity_block is None
    assert "■ Listener ready" in transcript
    assert "/listen start" not in transcript


@pytest.mark.anyio
async def test_listener_stop_hands_spinner_directly_to_stopped_result() -> None:
    runtime = TuiRuntime()
    listener = _OperationListener(running=True, ready=True)
    controller = _OperationController(runtime, listener)
    foreground = TuiForegroundTasks(runtime, controller)

    foreground.start_listener("stop")
    waiting = asyncio.create_task(foreground.wait())
    await controller.pause_started.wait()

    assert runtime.screen.activity_block is not None
    assert "Listener stopping" in fragments_text(
        runtime.screen.activity_block.fragments,
    )

    controller.pause_release.set()
    await waiting

    transcript = fragments_text(runtime.document.fragments(width=80))
    assert runtime.screen.activity_block is None
    assert "■ Listener stopped" in transcript
    assert "/listen stop" not in transcript


@pytest.mark.anyio
async def test_listener_start_failure_replaces_spinner_with_error() -> None:
    runtime = TuiRuntime()
    listener = _OperationListener(running=False, ready=False)
    controller = _OperationController(runtime, listener)
    foreground = TuiForegroundTasks(runtime, controller)

    async def fail_before_ready() -> None:
        raise RuntimeError("connection failed")

    listener.wait_until_ready = fail_before_ready
    foreground.start_listener("start")
    await foreground.wait()

    transcript = fragments_text(runtime.document.fragments(width=80))
    assert runtime.screen.activity_block is None
    assert "■ Listener failed" in transcript
    assert "  └ RuntimeError: connection failed" in transcript


@pytest.mark.anyio
async def test_listener_ready_timeout_stops_transport_and_reports_failure() -> None:
    runtime = TuiRuntime()
    listener = _OperationListener(running=False, ready=False)
    controller = _OperationController(runtime, listener)
    foreground = TuiForegroundTasks(runtime, controller)

    async def time_out_before_ready() -> None:
        raise TimeoutError("listener was not ready within 30s")

    listener.wait_until_ready = time_out_before_ready
    controller.pause_release.set()
    foreground.start_listener("start")
    await foreground.wait()

    transcript = fragments_text(runtime.document.fragments(width=80))
    assert controller.pause_started.is_set()
    assert not listener.is_running()
    assert runtime.screen.activity_block is None
    assert "■ Listener failed" in transcript
    assert "  └ TimeoutError: listener was not ready within 30s" in transcript


@pytest.mark.anyio
async def test_listener_start_interrupt_stops_transport_and_clears_spinner(
) -> None:
    runtime = TuiRuntime()
    listener = _OperationListener(running=False, ready=False)
    controller = _OperationController(runtime, listener)
    foreground = TuiForegroundTasks(runtime, controller)

    foreground.start_listener("start")
    waiting = asyncio.create_task(foreground.wait())
    await listener.wait_started.wait()

    controller.pause_release.set()
    assert foreground.handle_interrupt() is InterruptDisposition.CONSUMED
    await waiting

    transcript = fragments_text(runtime.document.fragments(width=80))
    assert controller.pause_started.is_set()
    assert not listener.is_running()
    assert runtime.screen.activity_block is None
    assert "• Listener · start interrupted" in transcript


def test_mailbox_count_and_header_are_bounded_and_spaced() -> None:
    assert format_mailbox_count(999) == "999"
    assert format_mailbox_count(1000) == "999+"

    runtime = TuiRuntime()
    runtime.set_mailbox_entries(
        tuple(_entry(index) for index in range(1000)),
        listener_active=True,
    )

    footer = _text(runtime.screen._footer_fragments())
    header = _text(runtime.screen._mailbox_overlay_header_fragments())

    assert "Mailbox 999+" in footer
    assert header.startswith("/ M A I L B O X / ")
    assert "999+ pending · listening" in header


def test_mailbox_overlay_reserves_codex_footer_spacing() -> None:
    runtime = TuiRuntime()
    runtime.screen._output_size = lambda: (40, 9)

    layout = runtime.screen._mailbox_overlay_layout()

    assert layout.header_height == 1
    assert layout.content_height == 4
    assert layout.footer_height == 4


@pytest.mark.anyio
async def test_mailbox_open_failure_restores_inline_renderer_state() -> None:
    runtime = TuiRuntime()
    screen = runtime.screen
    runtime.set_mailbox_entries((_entry(1),), listener_active=True)

    with patch.object(
        screen.mailbox_overlay,
        "_invalidate",
        side_effect=RuntimeError("render failed"),
    ), pytest.raises(RuntimeError, match="render failed"):
        screen.set_mailbox_overlay(True, entry_key="1")

    assert not screen.mailbox_overlay.active
    assert not screen.application.full_screen
    assert not screen.application.renderer.full_screen
    assert screen._inline_renderer_state is None
    assert screen.application.layout.current_control == screen.input.control


@pytest.mark.anyio
async def test_full_screen_overlays_are_mutually_exclusive() -> None:
    runtime = TuiRuntime()
    screen = runtime.screen
    runtime.set_mailbox_entries((_entry(1),), listener_active=True)

    assert screen.set_transcript_overlay(True)
    assert not screen.set_mailbox_overlay(True, entry_key="1")
    assert screen.transcript_overlay.active
    assert not screen.mailbox_overlay.active

    assert screen.set_transcript_overlay(False)
    assert screen.set_mailbox_overlay(True, entry_key="1")
    assert not screen.set_transcript_overlay(True)
    assert screen.mailbox_overlay.active
    assert not screen.transcript_overlay.active

    assert screen.set_mailbox_overlay(False)
