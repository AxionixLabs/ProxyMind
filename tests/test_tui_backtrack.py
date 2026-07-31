# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace

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
            mode="chat",
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


def test_successful_backtrack_installs_canonical_prompt() -> None:
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
    )

    loop._finish_transcript_backtrack(
        SimpleNamespace(attach=attach),
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
    text = "".join(
        value
        for _style, value in runtime.document.all_fragments(width=80)
    )
    assert text == "header"


if __name__ == '__main__':
    pass
