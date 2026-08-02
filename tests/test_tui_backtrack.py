# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace
from unittest.mock import (
    AsyncMock,
    Mock,
)

import pytest
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from mind_app.attach import Attach
from mind_app.interaction.contracts import PromptContext
from mind_app.tui.core.models import (
    FragmentBlock,
    TranscriptBacktrackRequest
)
from mind_app.tui.core.runtime import TuiRuntime
from mind_app.tui.core.submission import TuiTranscriptBacktrackRequested
from mind_app.tui.features.conversation import ForkLiveStatus
from mind_app.tui.session import loop
from mind_app.tui.session.state import TuiSessionState
from mind_core.permissions import preset_permissions
from mind_nova.requests.fork import ResubmittablePrompt


def _block(text: str) -> FragmentBlock:
    return FragmentBlock((("", text),))


def _append_turn(runtime: TuiRuntime, turn_id: str, prompt: str) -> None:
    runtime.append_block(_block(prompt), kind="user")
    assert runtime.bind_submitted_turn(turn_id, prompt)
    runtime.append_block(_block(f"answer for {prompt}"), kind="assistant")


@pytest.mark.anyio
async def test_transcript_selects_previous_prompt_and_emits_backtrack() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        runtime.screen._output_size = lambda: (60, 14)
        _append_turn(runtime, "turn_one", "first prompt")
        _append_turn(runtime, "turn_two", "second prompt")

        await runtime.open()
        prompt_task = asyncio.create_task(runtime.read_message(PromptContext(
            model="",
        )))
        try:
            pipe_input.send_text("\x14")
            for _ in range(100):
                await asyncio.sleep(0.01)
                if runtime.screen.transcript_overlay.active:
                    break

            pipe_input.send_text("\x1b")
            for _ in range(100):
                await asyncio.sleep(0.01)
                if runtime.screen.transcript_overlay.backtrack_active:
                    break

            assert runtime.screen.transcript_overlay.active
            assert runtime.screen.transcript_overlay.backtrack_active
            assert any(
                "transcript.overlay.selection" in style
                for style, _text in (
                    runtime.screen.transcript_overlay.visible_fragments()
                )
            )

            pipe_input.send_text("\x1b[D")
            await asyncio.sleep(0.05)
            pipe_input.send_text("\r")

            with pytest.raises(TuiTranscriptBacktrackRequested) as raised:
                await prompt_task
            assert raised.value.request == TranscriptBacktrackRequest(
                turn_id="turn_one",
                prompt="first prompt",
            )
            assert not runtime.screen.transcript_overlay.active
        finally:
            if not prompt_task.done():
                prompt_task.cancel()
                await asyncio.gather(prompt_task, return_exceptions=True)
            await runtime.close()


@pytest.mark.anyio
async def test_double_escape_opens_latest_backtrack_from_empty_input() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        _append_turn(runtime, "turn_one", "first prompt")
        await runtime.open()
        try:
            pipe_input.send_text("\x1b")
            for _ in range(100):
                await asyncio.sleep(0.01)
                if runtime.input_model.history_backtrack_primed:
                    break

            assert runtime.input_model.history_backtrack_primed
            assert not runtime.screen.transcript_overlay.active
            footer = "".join(
                text
                for _style, text in runtime.screen._footer_fragments()
            )
            assert footer == "Esc again to edit previous message"

            pipe_input.send_text("\x1b")
            for _ in range(100):
                await asyncio.sleep(0.01)
                if runtime.screen.transcript_overlay.active:
                    break

            assert runtime.screen.transcript_overlay.active
            assert runtime.screen.transcript_overlay.backtrack_active
            assert not runtime.input_model.history_backtrack_primed
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_typing_cancels_primed_history_backtrack() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        _append_turn(runtime, "turn_one", "first prompt")
        await runtime.open()
        try:
            pipe_input.send_text("\x1b")
            for _ in range(100):
                await asyncio.sleep(0.01)
                if runtime.input_model.history_backtrack_primed:
                    break
            pipe_input.send_text("x")
            for _ in range(100):
                await asyncio.sleep(0.01)
                if runtime.screen.input.buffer.text == "x":
                    break

            assert runtime.screen.input.buffer.text == "x"
            assert not runtime.input_model.history_backtrack_primed
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_double_escape_reports_missing_previous_message() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        await runtime.open()
        try:
            pipe_input.send_text("\x1b\x1b")
            for _ in range(100):
                await asyncio.sleep(0.01)
                if runtime.document.blocks:
                    break

            text = "".join(
                value
                for _style, value in runtime.document.all_fragments(width=80)
            )
            assert text == "No previous message to edit."
            assert not runtime.screen.transcript_overlay.active
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_transcript_escape_reports_missing_previous_message() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        await runtime.open()
        try:
            pipe_input.send_text("\x14")
            for _ in range(100):
                await asyncio.sleep(0.01)
                if runtime.screen.transcript_overlay.active:
                    break
            pipe_input.send_text("\x1b")
            for _ in range(100):
                await asyncio.sleep(0.01)
                if runtime.document.blocks:
                    break

            text = "".join(
                value
                for _style, value in runtime.document.all_fragments(width=80)
            )
            assert text == "No previous message to edit."
            assert not runtime.screen.transcript_overlay.active
        finally:
            await runtime.close()


def test_apply_transcript_backtrack_removes_selected_turn_and_tail() -> None:
    runtime = TuiRuntime()
    runtime.append_block(_block("header"), kind="system")
    _append_turn(runtime, "turn_one", "first prompt")
    _append_turn(runtime, "turn_two", "second prompt")

    changed = runtime.apply_transcript_backtrack(
        TranscriptBacktrackRequest(
            turn_id="turn_one",
            prompt="first prompt revised",
        )
    )

    assert changed
    text = "".join(
        value
        for _style, value in runtime.document.all_fragments(width=80)
    )
    assert text == "header"
    assert runtime.screen.input.buffer.text == "first prompt revised"
    assert runtime.document.scrollback_line_count == 0
    assert runtime.document.cleared_line_count == 0


def test_apply_transcript_backtrack_false_keeps_input_and_document() -> None:
    runtime = TuiRuntime()
    runtime.append_block(_block("header"), kind="system")
    _append_turn(runtime, "turn_one", "first prompt")
    runtime.replace_input_text("draft")

    changed = runtime.apply_transcript_backtrack(
        TranscriptBacktrackRequest(
            turn_id="missing_turn",
            prompt="canonical draft",
        )
    )

    assert not changed
    assert runtime.screen.input.buffer.text == "draft"
    assert [item.kind for item in runtime.document.blocks] == [
        "system",
        "user",
        "assistant",
    ]


def test_apply_transcript_backtrack_restores_state_after_viewport_error() -> None:
    runtime = TuiRuntime()
    runtime.append_block(_block("header"), kind="system")
    _append_turn(runtime, "turn_one", "first prompt")
    runtime.replace_input_text("draft")
    runtime.viewport.view_row = 4
    runtime.screen.clear_terminal_scrollback = Mock(
        side_effect=RuntimeError("terminal unavailable")
    )

    with pytest.raises(RuntimeError, match="terminal unavailable"):
        runtime.apply_transcript_backtrack(
            TranscriptBacktrackRequest(
                turn_id="turn_one",
                prompt="canonical draft",
            )
        )

    assert runtime.screen.input.buffer.text == "draft"
    assert runtime.viewport.view_row == 4
    assert [item.kind for item in runtime.document.blocks] == [
        "system",
        "user",
        "assistant",
    ]


def test_attachment_only_turn_creates_selectable_transcript_cell(tmp_path) -> None:
    runtime = TuiRuntime()
    image = tmp_path / "screen.png"
    image.write_bytes(b"image")

    changed = runtime.bind_submitted_turn(
        "turn_image",
        "",
        has_attachments=True,
        attachment_labels=(image.name,),
    )
    runtime.screen.transcript_overlay.open()

    assert changed
    assert runtime.screen.transcript_overlay.begin_or_step_backtrack()
    assert runtime.screen.transcript_overlay.confirm_backtrack() == (
        TranscriptBacktrackRequest(
            turn_id="turn_image",
            prompt="",
        )
    )
    text = "".join(
        value
        for _style, value in runtime.document.all_fragments(width=80)
    )
    assert text == "› [Attachment: screen.png]"


def test_transcript_request_keeps_structured_prompt_payload() -> None:
    runtime = TuiRuntime()
    _append_turn(runtime, "turn_one", "inspect this")
    runtime.bind_turn_payload(
        "turn_one",
        attachments=({
            "kind": "image",
            "image_url": "https://example.test/image.png",
        },),
        extras={"selection": {"x": 10, "y": 20}},
    )
    runtime.screen.transcript_overlay.open()

    assert runtime.screen.transcript_overlay.begin_or_step_backtrack()
    assert runtime.screen.transcript_overlay.confirm_backtrack() == (
        TranscriptBacktrackRequest(
            turn_id="turn_one",
            prompt="inspect this",
            attachments=({
                "kind": "image",
                "image_url": "https://example.test/image.png",
            },),
            extras={"selection": {"x": 10, "y": 20}},
        )
    )


def test_restored_attachments_are_resubmitted_without_reencoding() -> None:
    attach = Attach()
    attachments = [{
        "kind": "image",
        "image_url": "https://example.test/image.png",
        "file_key": "file_123",
        "metadata": {"page": 2},
    }, {
        "kind": "file",
        "filename": "notes.txt",
        "text": "source text",
    }]

    attach.replace_pending_attachments(attachments)
    attachments[0]["metadata"]["page"] = 9

    assert attach.has_pending_attachments()
    assert attach.pending_attachments_snapshot()[0]["metadata"] == {"page": 2}
    assert attach.consume_pending_attachments() == [{
        "kind": "image",
        "image_url": "https://example.test/image.png",
        "file_key": "file_123",
        "metadata": {"page": 2},
    }, {
        "kind": "file",
        "filename": "notes.txt",
        "text": "source text",
    }]
    assert not attach.has_pending_attachments()


@pytest.mark.anyio
async def test_successful_backtrack_installs_canonical_prompt() -> None:
    runtime = TuiRuntime()
    runtime.append_block(_block("header"), kind="system")
    _append_turn(runtime, "turn_one", "local prompt")
    attach = Attach()
    state = TuiSessionState(
        pref_config={},
        model="",
        workspace_label="",
        permissions=preset_permissions("auto"),
    )
    status = ForkLiveStatus()
    status.completed(
        0,
        prompt=ResubmittablePrompt(
            message="canonical prompt",
            attachments=({
                "kind": "file",
                "file_key": "file_123",
                "text": "source text",
            },),
            extras={"selection": {"x": 10, "y": 20}},
        ),
        source_session=("cid_source_12345678", "sid_source_1_abcdef"),
        target_session=("cid_target_87654321", "sid_target_2_fedcba"),
    )
    bound = []

    async def bind_conversation(cid, sid, *, source):
        bound.append((cid, sid, source))
        return {"cid": cid, "sid": sid}

    await loop._finish_transcript_backtrack(
        SimpleNamespace(
            attach=attach,
            bind_conversation=bind_conversation,
        ),
        runtime,
        state,
        TranscriptBacktrackRequest(
            turn_id="turn_one",
            prompt="local prompt",
        ),
        status,
    )

    assert runtime.screen.input.buffer.text == "canonical prompt"
    assert attach.consume_pending_attachments() == [{
        "kind": "file",
        "file_key": "file_123",
        "text": "source text",
    }]
    assert state.consume_pending_prompt_extras() == {
        "selection": {"x": 10, "y": 20},
    }
    assert bound == [(
        "cid_target_87654321",
        "sid_target_2_fedcba",
        "tui",
    )]
    text = "".join(
        value
        for _style, value in runtime.document.all_fragments(width=80)
    )
    assert text == "header"


@pytest.mark.anyio
async def test_backtrack_request_restores_full_local_draft_before_fork() -> None:
    runtime = TuiRuntime()
    attach = Attach()
    state = TuiSessionState(
        pref_config={},
        model="",
        workspace_label="",
        permissions=preset_permissions("auto"),
    )

    class ForegroundStub(object):
        def start(self, *_args, **_kwargs) -> None:
            return None

        async def wait(self) -> None:
            return None

    await loop._handle_transcript_backtrack(
        SimpleNamespace(attach=attach),
        runtime,
        state,
        ForegroundStub(),
        TranscriptBacktrackRequest(
            turn_id="turn_one",
            prompt="inspect this",
            attachments=({"kind": "file", "file_key": "file_123"},),
            extras={"selection": {"x": 10, "y": 20}},
        ),
    )

    assert runtime.screen.input.buffer.text == "inspect this"
    assert attach.consume_pending_attachments() == [{
        "kind": "file",
        "file_key": "file_123",
    }]
    assert state.consume_pending_prompt_extras() == {
        "selection": {"x": 10, "y": 20},
    }


@pytest.mark.anyio
async def test_backtrack_loop_rolls_back_and_keeps_full_draft_on_false_commit(
    monkeypatch,
) -> None:
    runtime = TuiRuntime()
    runtime.append_block(_block("header"), kind="system")
    _append_turn(runtime, "turn_one", "local prompt")
    runtime.apply_transcript_backtrack = Mock(return_value=False)

    attach = Attach()
    state = TuiSessionState(
        pref_config={},
        model="",
        workspace_label="",
        permissions=preset_permissions("auto"),
    )
    bound = []
    views = []

    async def bind_conversation(cid, sid, *, source):
        bound.append((cid, sid, source))
        return {"cid": cid, "sid": sid}

    mind = SimpleNamespace(
        attach=attach,
        bind_conversation=bind_conversation,
        frontend=SimpleNamespace(
            application=SimpleNamespace(emit=views.append),
        ),
    )
    status = ForkLiveStatus()
    status.completed(
        0,
        prompt=ResubmittablePrompt(
            message="canonical prompt",
            attachments=(),
            extras={},
        ),
        source_session=("cid_source_12345678", "sid_source_1_abcdef"),
        target_session=("cid_target_87654321", "sid_target_2_fedcba"),
    )

    class ForegroundStub(object):
        def start(self, _name, operation, **callbacks) -> None:
            self.operation = operation
            self.callbacks = callbacks

        async def wait(self) -> None:
            result = await self.operation()
            await self.callbacks["on_succeeded"](result)

    monkeypatch.setattr(
        loop,
        "fork_current_conversation",
        AsyncMock(return_value=status),
    )

    request = TranscriptBacktrackRequest(
        turn_id="turn_one",
        prompt="local prompt",
        attachments=({"kind": "file", "file_key": "file_123"},),
        extras={"selection": {"x": 10, "y": 20}},
    )
    await loop._handle_transcript_backtrack(
        mind,
        runtime,
        state,
        ForegroundStub(),
        request,
    )

    assert bound == [
        ("cid_target_87654321", "sid_target_2_fedcba", "tui"),
        (
            "cid_source_12345678",
            "sid_source_1_abcdef",
            "tui:backtrack-rollback",
        ),
    ]
    assert runtime.screen.input.buffer.text == "local prompt"
    assert attach.consume_pending_attachments() == [{
        "kind": "file",
        "file_key": "file_123",
    }]
    assert state.consume_pending_prompt_extras() == {
        "selection": {"x": 10, "y": 20},
    }
    assert any(view.type == "tui.fork.status" for view in views)


@pytest.mark.anyio
async def test_backtrack_rolls_conversation_back_if_local_commit_fails() -> None:
    runtime = TuiRuntime()
    _append_turn(runtime, "turn_one", "local prompt")
    runtime.apply_transcript_backtrack = Mock(
        side_effect=RuntimeError("terminal unavailable")
    )
    views = []
    bound = []
    attach = Attach()
    state = TuiSessionState(
        pref_config={},
        model="",
        workspace_label="",
        permissions=preset_permissions("auto"),
    )
    status = ForkLiveStatus()
    status.completed(
        0,
        prompt=ResubmittablePrompt(
            message="canonical prompt",
            attachments=(),
            extras={},
        ),
        source_session=("cid_source_12345678", "sid_source_1_abcdef"),
        target_session=("cid_target_87654321", "sid_target_2_fedcba"),
    )

    async def bind_conversation(cid, sid, *, source):
        bound.append((cid, sid, source))
        return {"cid": cid, "sid": sid}

    await loop._finish_transcript_backtrack(
        SimpleNamespace(
            attach=attach,
            bind_conversation=bind_conversation,
            frontend=SimpleNamespace(
                application=SimpleNamespace(emit=views.append),
            ),
        ),
        runtime,
        state,
        TranscriptBacktrackRequest(
            turn_id="turn_one",
            prompt="local prompt",
        ),
        status,
    )

    assert bound == [
        ("cid_target_87654321", "sid_target_2_fedcba", "tui"),
        (
            "cid_source_12345678",
            "sid_source_1_abcdef",
            "tui:backtrack-rollback",
        ),
    ]
    assert any(view.type == "tui.fork.status" for view in views)


if __name__ == '__main__':
    pass
