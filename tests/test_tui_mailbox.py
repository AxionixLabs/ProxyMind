# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from mind_app.interaction.contracts import PromptContext
from mind_app.subscription.forwarding import AgentInbox
from mind_app.subscription.models import AgentForwardRequest
from mind_app.tui.core.models import (
    FragmentBlock,
    MailboxRunRequest,
    MenuDescriptionLayout,
    STANDARD_MENU_FOOTER_HINT,
)
from mind_app.tui.core.render import fragments_text
from mind_app.tui.core.runtime import TuiRuntime
from mind_app.tui.core.submission import TuiMailboxRunRequested
from mind_app.tui.features.mailbox import (
    PreparedMailboxRun,
    TuiMailboxFeature
)
from mind_app.tui.session.loop import _handle_mailbox_run


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


class _Listener(object):
    def __init__(
        self,
        *requests: AgentForwardRequest,
        running: bool = True,
        ready: bool = True,
    ) -> None:
        self.inbox = AgentInbox()
        for request in requests:
            self.inbox.add(request)
        self.running = running
        self.ready = ready
        self.callback = None
        self.receipt_disposition_resolver = None

    def is_running(self) -> bool:
        return self.running

    def is_ready(self) -> bool:
        return self.running and self.ready

    def bind_inbox_changed(self, callback) -> None:
        self.callback = callback
        if callback is not None:
            callback()

    def bind_receipt_disposition(self, resolver) -> None:
        self.receipt_disposition_resolver = resolver

    async def discard(self, message_id: str):
        item = self.inbox.remove(message_id)
        if self.callback is not None:
            self.callback()
        return item


def _feature(
    listener: _Listener | None,
) -> tuple[TuiMailboxFeature, TuiRuntime, list]:
    views = []
    runtime = TuiRuntime()
    controller = SimpleNamespace(
        subscription_runtime=listener,
        frontend=SimpleNamespace(
            application=SimpleNamespace(emit=views.append),
        ),
    )
    feature = TuiMailboxFeature(runtime, controller)
    feature.bind_listener()
    return feature, runtime, views


async def _wait_for_menu(runtime: TuiRuntime, title: str) -> None:
    for _ in range(100):
        state = runtime.screen.menu.state
        if state is not None and state.request.title == title:
            return None
        await asyncio.sleep(0)
    raise AssertionError(f"menu did not open: {title}")


@pytest.mark.anyio
async def test_mailbox_summary_uses_command_description() -> None:
    feature, runtime, _views = _feature(_Listener(
        _request("1", "first line", summary="Inspect workspace"),
        _request("2", "run tests"),
    ))
    task = asyncio.create_task(feature.open())
    await _wait_for_menu(runtime, "Mailbox")
    state = runtime.screen.menu.state
    assert state is not None
    request = state.request
    assert request.title == "Mailbox"
    assert request.title_accent_suffix == ""
    assert request.status == "View and process remote request messages."
    assert request.body == ()
    assert request.selected == 2
    assert request.view_id == "mailbox:summary"
    assert request.help_text == ""
    assert request.footer_hint == STANDARD_MENU_FOOTER_HINT
    assert (
        request.description_layout
        is MenuDescriptionLayout.STACK_BELOW_WHEN_NARROW
    )
    assert [option.label for option in request.options] == [
        "Auto-run: on",
        "Auto-run: off",
        "Inspect workspace",
        "run tests",
    ]
    assert [option.value for option in request.options[:2]] == [
        ("auto", True),
        ("auto", False),
    ]
    runtime.cancel_menu()
    await task


@pytest.mark.anyio
async def test_mailbox_message_detail_returns_to_actions_then_queues_run() -> None:
    feature, runtime, _views = _feature(_Listener(
        _request("1", "inspect workspace", summary="Inspect"),
    ))
    task = asyncio.create_task(feature.open())
    await _wait_for_menu(runtime, "Mailbox")
    runtime.screen.menu._choose_index(2)
    await _wait_for_menu(runtime, "Mailbox Message")
    state = runtime.screen.menu.state
    assert state is not None
    assert state.request.status == "Inspect"
    assert [option.label for option in state.request.options] == [
        "Run",
        "Delete",
        "Detail",
    ]
    runtime.screen.menu._choose_index(2)
    for _ in range(100):
        if runtime.screen.mailbox_overlay.active:
            break
        await asyncio.sleep(0)
    assert runtime.screen.mailbox_overlay.active
    runtime.close_mailbox_overlay()
    await _wait_for_menu(runtime, "Mailbox Message")
    assert runtime.screen.menu.state is not None
    assert runtime.screen.menu.state.request.title == "Mailbox Message"
    runtime.screen.menu._choose_index(0)
    await task
    queued = await runtime.submissions.read_submission()
    assert queued == MailboxRunRequest("1", automatic=False)


@pytest.mark.anyio
async def test_mailbox_real_menu_detail_returns_to_same_application() -> None:
    feature, runtime, _views = _feature(_Listener(
        _request("1", "inspect workspace", summary="Inspect"),
    ))
    application = runtime.screen.application

    task = asyncio.create_task(feature.open())
    await _wait_for_menu(runtime, "Mailbox")
    runtime.screen.menu._choose_index(2)

    await _wait_for_menu(runtime, "Mailbox Message")
    runtime.screen.menu._choose_index(2)
    for _ in range(100):
        if runtime.screen.mailbox_overlay.active:
            break
        await asyncio.sleep(0)

    assert runtime.screen.application is application
    assert runtime.screen.mailbox_overlay.active
    assert application.full_screen

    runtime.close_mailbox_overlay()
    await _wait_for_menu(runtime, "Mailbox Message")
    runtime.screen.menu._choose_index(0)
    await task

    assert not runtime.screen.mailbox_overlay.active
    assert not application.full_screen
    assert await runtime.submissions.read_submission() == MailboxRunRequest(
        "1",
        automatic=False,
    )


@pytest.mark.anyio
async def test_mailbox_delete_cancels_and_removes_summary() -> None:
    listener = _Listener(_request("1", "inspect workspace"))
    feature, runtime, views = _feature(listener)
    task = asyncio.create_task(feature.open())
    await _wait_for_menu(runtime, "Mailbox")
    runtime.screen.menu._choose_index(2)
    await _wait_for_menu(runtime, "Mailbox Message")
    runtime.screen.menu._choose_index(1)
    await task

    assert listener.inbox.items == []
    assert runtime.mailbox_entries() == ()
    assert views[0].renderable.plain_text == "■ Mailbox message deleted"


@pytest.mark.anyio
async def test_mailbox_auto_queues_one_ready_message_at_a_time() -> None:
    listener = _Listener(
        _request("1", "first"),
        _request("2", "second"),
    )
    feature, runtime, _views = _feature(listener)

    feature.set_auto_run(True)
    first = await runtime.submissions.read_submission()

    assert first == MailboxRunRequest("1", automatic=True)
    assert runtime.submissions.message_queue.empty()

    listener.inbox.remove("1")
    feature.finish_run(first)
    second = await runtime.submissions.read_submission()

    assert second == MailboxRunRequest("2", automatic=True)
    assert runtime.submissions.message_queue.empty()


@pytest.mark.anyio
async def test_mailbox_manual_run_does_not_duplicate_queued_auto_run() -> None:
    listener = _Listener(_request("1", "first"))
    feature, runtime, _views = _feature(listener)
    feature.set_auto_run(True)
    task = asyncio.create_task(feature.open())
    await _wait_for_menu(runtime, "Mailbox")
    runtime.screen.menu._choose_index(2)
    await _wait_for_menu(runtime, "Mailbox Message")
    runtime.screen.menu._choose_index(0)
    await task

    request = await runtime.submissions.read_submission()
    assert request == MailboxRunRequest("1", automatic=True)
    assert runtime.submissions.message_queue.empty()


@pytest.mark.anyio
async def test_mailbox_auto_does_not_duplicate_queued_manual_run() -> None:
    listener = _Listener(_request("1", "first"))
    feature, runtime, _views = _feature(listener)
    task = asyncio.create_task(feature.open())
    await _wait_for_menu(runtime, "Mailbox")
    runtime.screen.menu._choose_index(2)
    await _wait_for_menu(runtime, "Mailbox Message")
    runtime.screen.menu._choose_index(0)
    await task
    feature.set_auto_run(True)

    request = await runtime.submissions.read_submission()
    assert request == MailboxRunRequest("1", automatic=False)
    prepared = feature.prepare_run(request)
    assert prepared == PreparedMailboxRun(listener, "1", "first")
    assert runtime.submissions.message_queue.empty()


@pytest.mark.anyio
async def test_mailbox_auto_resumes_once_after_listener_restart() -> None:
    listener = _Listener(_request("1", "first"))
    feature, runtime, _views = _feature(listener)
    feature.set_auto_run(True)
    request = await runtime.submissions.read_submission()

    listener.running = False
    listener.ready = False
    listener.callback()

    assert feature.prepare_run(request) is None
    feature.finish_run(request)
    assert listener.inbox.pending_count() == 1
    assert runtime.submissions.message_queue.empty()

    listener.running = True
    listener.callback()
    assert runtime.submissions.message_queue.empty()

    listener.ready = True
    listener.callback()
    resumed = await runtime.submissions.read_submission()

    assert resumed == MailboxRunRequest("1", automatic=True)
    assert runtime.submissions.message_queue.empty()


@pytest.mark.anyio
async def test_mailbox_auto_waits_for_listener_ready() -> None:
    listener = _Listener(
        _request("1", "first"),
        ready=False,
    )
    feature, runtime, _views = _feature(listener)

    feature.set_auto_run(True)

    assert runtime.submissions.message_queue.empty()

    listener.ready = True
    listener.callback()
    request = await runtime.submissions.read_submission()

    assert request == MailboxRunRequest("1", automatic=True)


@pytest.mark.anyio
async def test_mailbox_queued_auto_request_is_skipped_after_disabling() -> None:
    listener = _Listener(_request("1", "first"))
    feature, runtime, _views = _feature(listener)
    feature.set_auto_run(True)
    request = await runtime.submissions.read_submission()

    feature.set_auto_run(False)

    assert feature.prepare_run(request) is None
    feature.finish_run(request)
    assert listener.inbox.pending_count() == 1
    assert runtime.submissions.message_queue.empty()


@pytest.mark.anyio
async def test_mailbox_auto_binds_listener_started_after_tui() -> None:
    feature, runtime, _views = _feature(None)
    feature.set_auto_run(True)
    listener = _Listener(_request("1", "first"))

    feature.controller.subscription_runtime = listener
    feature.bind_listener()

    assert await runtime.submissions.read_submission() == MailboxRunRequest(
        "1",
        automatic=True,
    )


@pytest.mark.anyio
async def test_mailbox_run_request_is_not_staged_as_visible_input() -> None:
    runtime = TuiRuntime()
    runtime.enqueue_mailbox_run("message-1", automatic=True)

    with pytest.raises(TuiMailboxRunRequested) as requested:
        await runtime.read_message(PromptContext(model="test"))

    assert requested.value.request == MailboxRunRequest(
        "message-1",
        automatic=True,
    )
    assert not runtime.document.blocks


@pytest.mark.anyio
async def test_mailbox_auto_toggle_is_process_local_and_visible() -> None:
    listener = _Listener(ready=False)
    feature, runtime, views = _feature(listener)
    task = asyncio.create_task(feature.open())
    await _wait_for_menu(runtime, "Mailbox")
    assert runtime.screen.menu.state is not None
    assert runtime.screen.menu.state.request.selected == 1
    runtime.screen.menu._choose_index(0)
    await task

    assert feature.auto_run
    assert listener.receipt_disposition_resolver() == "auto_run"
    assert "".join(
        text for _style, text in views[0].renderable.fragments
    ) == "• Mailbox auto-run enabled"
    assert runtime.submissions.message_queue.empty()


@pytest.mark.anyio
async def test_mailbox_auto_run_can_be_explicitly_disabled() -> None:
    listener = _Listener(ready=False)
    feature, runtime, views = _feature(listener)
    feature.set_auto_run(True)
    task = asyncio.create_task(feature.open())
    await _wait_for_menu(runtime, "Mailbox")
    assert runtime.screen.menu.state is not None
    assert runtime.screen.menu.state.request.selected == 0
    runtime.screen.menu._choose_index(1)
    await task

    assert not feature.auto_run
    assert listener.receipt_disposition_resolver() == "queued"
    assert "".join(
        text for _style, text in views[0].renderable.fragments
    ) == "• Mailbox auto-run disabled"


@pytest.mark.anyio
async def test_mailbox_run_uses_main_tui_execution_lifecycle() -> None:
    runtime = TuiRuntime()
    started = asyncio.Event()
    release = asyncio.Event()

    turn_ids = []

    async def run_message(message_id: str, *, turn_id: str) -> None:
        assert message_id == "message-1"
        turn_ids.append(turn_id)
        assert runtime.execution_active
        started.set()
        await release.wait()

    listener = SimpleNamespace(run_message=run_message)
    mailbox = SimpleNamespace(
        prepare_run=Mock(return_value=PreparedMailboxRun(
            listener,
            "message-1",
            "inspect workspace\nthen run tests",
        )),
        finish_run=Mock(),
    )
    dispatcher = SimpleNamespace(
        mailbox=mailbox,
        application=SimpleNamespace(emit=Mock()),
        handle_stream_command=lambda *_args: False,
    )
    mind = SimpleNamespace(task_event=asyncio.Event())
    request = MailboxRunRequest("message-1", automatic=True)

    task = asyncio.create_task(
        _handle_mailbox_run(mind, runtime, dispatcher, request)
    )
    await started.wait()

    assert runtime.execution_active
    assert len(runtime.document.blocks) == 1
    query = runtime.document.blocks[0]
    assert query.kind == "user"
    assert query.raw_text == "inspect workspace\nthen run tests"
    assert query.prompt == query.raw_text
    assert query.turn_id == turn_ids[0]
    assert "inspect workspace" in fragments_text(
        query.transcript_block.fragments
    )

    release.set()
    await task

    assert not runtime.execution_active
    mailbox.finish_run.assert_called_once_with(request)


@pytest.mark.anyio
async def test_mailbox_run_failure_is_rendered_and_releases_auto_slot() -> None:
    runtime = TuiRuntime()
    views = []

    async def run_message(_message_id: str, *, turn_id: str) -> None:
        assert turn_id
        raise RuntimeError("connection closed")

    request = MailboxRunRequest("message-1", automatic=True)
    mailbox = SimpleNamespace(
        prepare_run=Mock(return_value=PreparedMailboxRun(
            SimpleNamespace(run_message=run_message),
            "message-1",
            "failing remote query",
        )),
        finish_run=Mock(),
    )
    dispatcher = SimpleNamespace(
        mailbox=mailbox,
        application=SimpleNamespace(emit=views.append),
        handle_stream_command=lambda *_args: False,
    )
    mind = SimpleNamespace(
        task_event=asyncio.Event(),
        frontend=SimpleNamespace(
            application=SimpleNamespace(emit=views.append),
        ),
    )

    await _handle_mailbox_run(mind, runtime, dispatcher, request)

    assert views[0].renderable.plain_text == (
        "■ Mailbox run failed\n"
        "  └ RuntimeError: connection closed"
    )
    assert runtime.document.blocks[0].raw_text == "failing remote query"
    mailbox.finish_run.assert_called_once_with(request)


@pytest.mark.anyio
async def test_mailbox_run_keeps_query_before_approval_tools_and_long_answer() -> None:
    runtime = TuiRuntime()
    runtime.screen._output_size = lambda: (28, 16)
    runtime.append_block(
        FragmentBlock((("", "prior answer\n" * 50),)),
        kind="assistant",
    )
    prompt = "!echo remote\n" + "请分析👩‍💻" * 30

    async def run_message(_message_id: str, *, turn_id: str) -> None:
        assert runtime.document.blocks[1].turn_id == turn_id

        approval = asyncio.create_task(runtime.screen.approval.request({
            "tool": "shell_command",
            "command": "printf mailbox-ok",
            "show_timer": False,
        }))
        await asyncio.sleep(0)
        assert runtime.screen.approval.active
        runtime.screen.approval.finish("accept")
        assert await approval == "accept"

        runtime.append_block(
            FragmentBlock((("", "• Running first tool"),)),
            kind="operation",
        )
        runtime.append_block(
            FragmentBlock((("", "• Ran second tool"),)),
            kind="operation",
        )
        runtime.set_active_renderable(
            FragmentBlock((("", "assistant " + "line\n" * 40),)),
            kind="assistant",
        )
        runtime.commit_active_renderable(
            FragmentBlock((("", "assistant " + "line\n" * 40),)),
            raw_text="assistant " + "line\n" * 40,
        )

    listener = SimpleNamespace(run_message=run_message)
    mailbox = SimpleNamespace(
        prepare_run=Mock(return_value=PreparedMailboxRun(
            listener,
            "message-combined",
            prompt,
        )),
        finish_run=Mock(),
    )
    dispatcher = SimpleNamespace(
        mailbox=mailbox,
        application=SimpleNamespace(emit=Mock()),
        handle_stream_command=lambda *_args: False,
    )
    mind = SimpleNamespace(task_event=asyncio.Event())
    request = MailboxRunRequest("message-combined", automatic=False)

    await _handle_mailbox_run(mind, runtime, dispatcher, request)

    cells = runtime.document.blocks
    assert [cell.kind for cell in cells] == [
        "assistant",
        "user",
        "operation",
        "operation",
        "assistant",
    ]
    assert cells[1].raw_text == prompt
    assert cells[1].prompt == prompt
    assert fragments_text(cells[1].display_block.fragments).startswith(
        "› !echo remote"
    )
    assert "lines Ctrl+T" in fragments_text(cells[1].display_block.fragments)
    assert not runtime.screen.approval.active
    assert not runtime.execution_active
    mailbox.finish_run.assert_called_once_with(request)


@pytest.mark.anyio
async def test_mailbox_interrupt_keeps_one_query_and_releases_execution() -> None:
    runtime = TuiRuntime()
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def run_message(_message_id: str, *, turn_id: str) -> None:
        assert runtime.document.blocks[0].turn_id == turn_id
        started.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    listener = SimpleNamespace(run_message=run_message)
    mailbox = SimpleNamespace(
        prepare_run=Mock(return_value=PreparedMailboxRun(
            listener,
            "message-interrupt",
            "remote query to interrupt",
        )),
        finish_run=Mock(),
    )
    emit = Mock()
    dispatcher = SimpleNamespace(
        mailbox=mailbox,
        application=SimpleNamespace(emit=emit),
        handle_stream_command=lambda *_args: False,
    )
    mind = SimpleNamespace(task_event=asyncio.Event())
    request = MailboxRunRequest("message-interrupt", automatic=True)

    task = asyncio.create_task(
        _handle_mailbox_run(mind, runtime, dispatcher, request)
    )
    await started.wait()
    runtime.submissions.interrupt_input()
    await task

    assert cancelled.is_set()
    assert len(runtime.document.blocks) == 1
    assert runtime.document.blocks[0].raw_text == "remote query to interrupt"
    assert not runtime.execution_active
    assert emit.call_args.args[0].type == "tui.interrupted"
    mailbox.finish_run.assert_called_once_with(request)
