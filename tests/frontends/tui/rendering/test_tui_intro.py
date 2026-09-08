# -*- coding: utf-8 -*-

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from agent.ports.presentation import ApplicationView
from frontends.tui.adapters import application as tui_application
from frontends.tui.adapters.application import TuiApplicationSink
from frontends.tui.core.render import fragments_text
from frontends.tui.core.runtime import TuiRuntime
from frontends.terminal.intro import intro_frames
from metadata import const


@pytest.mark.anyio
async def test_tui_intro_animates_and_commits_one_stable_block(monkeypatch) -> None:
    runtime = TuiRuntime()
    sink = TuiApplicationSink(runtime)
    rendered = []
    delays = []
    original_set_active = runtime.set_active_renderable

    def capture_frame(block, *, kind="assistant") -> None:
        rendered.append(fragments_text(block.fragments))
        original_set_active(block, kind=kind)

    async def skip_delay(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(runtime, "set_active_renderable", capture_frame)
    monkeypatch.setattr(tui_application.asyncio, "sleep", skip_delay)

    sink.emit(ApplicationView(type="intro"))
    sink.emit(ApplicationView(type="intro"))

    assert sink.pending_views == []

    await runtime._play_startup_animation()

    frames = intro_frames(const.APP_DESC)
    final_text = f">_ {const.APP_DESC} (v{const.APP_VERSION})"

    assert rendered[0] == ">_"
    assert rendered[1] == "> "
    assert rendered[-1] == final_text
    assert delays == [frame.delay_after for frame in frames]
    assert runtime.document.active_block is None
    assert len(runtime.document.blocks) == 1
    assert runtime.document.blocks[0].kind == "system"
    assert fragments_text(runtime.document.blocks[0].display_block.fragments) == final_text

    await runtime._play_startup_animation()
    assert len(runtime.document.blocks) == 1


@pytest.mark.anyio
async def test_tui_intro_cancellation_clears_active_block(monkeypatch) -> None:
    runtime = TuiRuntime()
    sink = TuiApplicationSink(runtime)

    async def cancel_delay(_delay: float) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(tui_application.asyncio, "sleep", cancel_delay)
    sink.emit(ApplicationView(type="intro"))

    with pytest.raises(asyncio.CancelledError):
        await runtime._play_startup_animation()

    assert runtime.document.active_block is None
    assert runtime.document.blocks == []


@pytest.mark.anyio
async def test_startup_review_commits_final_intro_without_animation(
    monkeypatch,
) -> None:
    runtime = TuiRuntime()
    sink = TuiApplicationSink(runtime)
    delay = AsyncMock()
    monkeypatch.setattr(tui_application.asyncio, "sleep", delay)

    sink.emit(ApplicationView(type="intro"))
    runtime.begin_startup_gate()

    await runtime.settle_startup_gate()

    assert not runtime.startup_gate_active
    delay.assert_not_awaited()
    assert runtime.document.active_block is None
    assert [
        fragments_text(item.display_block.fragments)
        for item in runtime.document.blocks
    ] == [f">_ {const.APP_DESC} (v{const.APP_VERSION})"]


@pytest.mark.anyio
async def test_startup_gate_does_not_clear_without_startup_surface() -> None:
    with create_pipe_input() as input_obj:
        runtime = TuiRuntime(input_obj=input_obj, output_obj=DummyOutput())
        runtime.begin_startup_gate()
        renderer = runtime.screen.application.renderer

        with patch.object(renderer, "clear", wraps=renderer.clear) as clear:
            await runtime.open()
            try:
                assert clear.call_count == 0

                await runtime.finish_startup_gate()
                for _ in range(20):
                    await asyncio.sleep(0)

                assert clear.call_count == 0
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_runtime_open_waits_for_intro_before_flushing_pending_views(
    monkeypatch,
) -> None:
    frames = tuple(
        replace(frame, delay_after=0.0)
        for frame in intro_frames(const.APP_DESC)
    )
    monkeypatch.setattr(tui_application, "intro_frames", lambda _title: frames)

    with create_pipe_input() as input_obj:
        runtime = TuiRuntime(input_obj=input_obj, output_obj=DummyOutput())
        sink = TuiApplicationSink(runtime)
        sink.emit(ApplicationView(type="intro"))
        sink.emit(ApplicationView(type="notice", renderable="after intro"))

        await runtime.open()

        assert runtime.document.active_block is None
        assert [
            fragments_text(item.display_block.fragments)
            for item in runtime.document.blocks
        ] == [
            f">_ {const.APP_DESC} (v{const.APP_VERSION})",
            "after intro",
        ]

        await runtime.close()


if __name__ == '__main__':
    pass
