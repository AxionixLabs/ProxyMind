# -*- coding: utf-8 -*-

"""验证终端 resize 后的稳定内容回流与同步输出事务。"""


import asyncio
import io
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import (
    AsyncMock,
    Mock,
    PropertyMock,
    call,
    patch,
)
import pytest
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.output.vt100 import Vt100_Output
from agent.ports import (
    AssistantBuffered,
    AssistantOutputBoundary,
    AssistantSegmentCompleted,
    AssistantTextDelta,
    ModelWaitRequested,
    OutputSurfaceContext,
    ResponseIdentity,
    SourcesOutput,
)
from frontends.tui.adapters.output import TuiOutputControl
from frontends.tui.adapters.session import create_tui_output_session
from frontends.tui.core.document import (
    TranscriptBlock,
    TuiBlockKind,
    TuiDocument,
)
from frontends.tui.core.render import (
    display_line_count,
    fragment_continuation_widths,
    fragments_text,
    join_formatted_lines,
    sanitize_formatted_text,
    split_formatted_lines,
    wrap_formatted_lines,
)
from frontends.tui.core.runtime import TuiRuntime
from tests.frontends.tui.rendering.frame_scenarios import (
    block as _block,
    document_text as _document_text,
    render_next_frame as _render_next_frame,
    transcript_text as _transcript_text,
    wait_for_scrollback_advance as _wait_for_scrollback_advance,
)


RESPONSE_IDENTITY = ResponseIdentity("turn_test", 1, 1, 1)


OUTPUT_SURFACE_CONTEXT = OutputSurfaceContext(
    surface_id="surface_test",
    cid="cid_test",
    sid="sid_test",
    turn_id="turn_test",
    agent_id="root",
)


@pytest.mark.anyio
async def test_scrollback_commits_oversized_replies_as_complete_blocks() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        terminal_size = Size(rows=30, columns=40)

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            side_effect=lambda: terminal_size,
        ):
            await runtime.open()
            try:
                runtime.set_execution_active(True)
                runtime.append_block(_block("approved"), kind="approval")
                runtime.append_block(_block("tool result"), kind="operation")
                runtime.append_block(
                    _block("\n".join(f"first {index}" for index in range(30))),
                    kind="assistant",
                )
                runtime.append_block(_block("Finished first"), kind="system")

                terminal_size = Size(rows=10, columns=40)
                runtime.set_execution_active(False)
                await asyncio.sleep(0.12)

                assert runtime.document.scrollback_line_count == (
                    runtime.document.stable_line_count
                )
                assert _document_text(runtime.document) == ""
                assert "first 29" in fragments_text(
                    runtime.document.all_fragments(width=40)
                )

                runtime.set_execution_active(True)
                runtime.append_block(_block("next question"), kind="user")
                runtime.append_block(
                    _block("\n".join(f"second {index}" for index in range(30))),
                    kind="assistant",
                )
                runtime.append_block(_block("Finished second"), kind="system")
                runtime.set_execution_active(False)
                await asyncio.sleep(0.02)

                assert runtime.document.scrollback_line_count == (
                    runtime.document.stable_line_count
                )
                visible = _document_text(runtime.document)
                assert "first 29" not in visible
                assert "second 29" not in visible
                assert visible == ""
                assert "second 29" in fragments_text(
                    runtime.document.all_fragments(width=40)
                )
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_single_oversized_block_retires_as_one_complete_block() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        source = "\n".join(f"line {index:02d}" for index in range(80))
        expected = "\n".join([
            "• line 00",
            *(f"  line {index:02d}" for index in range(1, 80)),
        ])
        output = TuiOutputControl("", runtime=runtime, animate=False)

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=12, columns=40),
        ):
            await runtime.open()
            try:
                with patch.object(
                    runtime.screen.application,
                    "print_text",
                    wraps=runtime.screen.application.print_text,
                ) as print_text:
                    runtime.set_execution_active(True)
                    await output.append_assistant_delta(source)
                    await output.settle_stream()
                    await output.prepare_external_output()
                    await _render_next_frame(runtime)
                    await _wait_for_scrollback_advance(runtime)

                assert print_text.call_count == 1

                printed = "".join(
                    text for _style, text in print_text.call_args.args[0]
                )
                visible = _document_text(runtime.document)

                assert printed.startswith("• line 00\n")
                assert printed.endswith("  line 79\n")
                assert printed == expected + "\n"
                assert visible == ""
                assert (
                    runtime.document.scrollback_line_count
                    == runtime.document.stable_line_count
                )
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_completed_scrollback_collapses_retired_canvas_height() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=12, columns=40),
        ):
            await runtime.open()
            try:
                runtime.append_block(
                    _block("\n".join(f"line {index}" for index in range(30))),
                    kind="assistant",
                )

                for _ in range(100):
                    await asyncio.sleep(0.002)
                    if runtime.document.scrollback_line_count > 0:
                        break

                screen = await _render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions

                assert runtime.document.scrollback_line_count > 0
                assert runtime.screen._visible_height() == (
                    runtime.screen._natural_visible_height()
                )
                assert runtime.screen.canvas_spacer not in positions
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_complete_block_scrollback_resize_never_reprints_content() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        terminal_size = Size(rows=12, columns=40)
        source = "\n".join(f"entry {index:02d}" for index in range(60))

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            side_effect=lambda: terminal_size,
        ):
            await runtime.open()
            try:
                with patch.object(
                    runtime.screen.application,
                    "print_text",
                    wraps=runtime.screen.application.print_text,
                ) as print_text:
                    runtime.append_block(_block(source), kind="assistant")
                    await asyncio.sleep(0.02)
                    first_count = runtime.document.scrollback_line_count
                    assert first_count == runtime.document.stable_line_count

                    terminal_size = Size(rows=20, columns=40)
                    runtime.viewport.schedule_scrollback_flush()
                    await asyncio.sleep(0.02)
                    assert runtime.document.scrollback_line_count == first_count

                    terminal_size = Size(rows=8, columns=40)
                    runtime.viewport.schedule_scrollback_flush()
                    await asyncio.sleep(0.02)

                chunks = [
                    "".join(text for _style, text in call.args[0])
                    for call in print_text.call_args_list
                ]
                visible = _document_text(runtime.document)

                assert chunks == [source + "\n"]
                assert visible == ""
                assert runtime.document.scrollback_line_count == first_count
                assert "".join(
                    text
                    for _style, text in runtime.document.all_fragments(width=40)
                ) == source
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_width_resize_reflows_native_scrollback_from_document() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        terminal_size = Size(rows=10, columns=40)
        source = "\n".join(f"entry {index:02d}" for index in range(60))

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            side_effect=lambda: terminal_size,
        ):
            await runtime.open()
            try:
                runtime.append_block(_block(source), kind="assistant")
                await asyncio.sleep(0.02)
                assert runtime.document.scrollback_line_count > 0

                with patch.object(
                    runtime.screen,
                    "clear_terminal_for_resize_replay",
                ) as clear, patch.object(
                    runtime.screen.application,
                    "print_text",
                    wraps=runtime.screen.application.print_text,
                ) as print_text:
                    terminal_size = Size(rows=10, columns=24)
                    runtime.viewport.observe_terminal_geometry(24, 10)
                    await asyncio.sleep(0.12)

                chunks = [
                    "".join(text for _style, text in call.args[0])
                    for call in print_text.call_args_list
                ]
                visible = _document_text(runtime.document)

                clear.assert_called_once_with()
                assert chunks
                assert chunks == [source + "\n"]
                assert visible == ""
                assert runtime.viewport._reflowed_geometry == (24, 10)
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_width_resize_reflow_waits_for_full_screen_overlay() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        terminal_size = Size(rows=8, columns=40)

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            side_effect=lambda: terminal_size,
        ):
            await runtime.open()
            try:
                runtime.append_block(
                    _block("\n".join(f"entry {index}" for index in range(40))),
                    kind="assistant",
                )
                await asyncio.sleep(0.02)
                assert runtime.document.scrollback_line_count > 0

                runtime.toggle_transcript_overlay()

                with patch.object(
                    runtime.screen,
                    "clear_terminal_for_resize_replay",
                ) as clear:
                    terminal_size = Size(rows=8, columns=24)
                    runtime.viewport.observe_terminal_geometry(24, 8)
                    await asyncio.sleep(0.12)
                    clear.assert_not_called()

                    runtime.toggle_transcript_overlay()
                    await asyncio.sleep(0.02)

                clear.assert_called_once_with()
                assert runtime.viewport._reflowed_geometry == (24, 8)
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_height_only_resize_reflows_native_scrollback() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        terminal_size = Size(rows=8, columns=40)
        source = "\n".join(f"entry {index:02d}" for index in range(60))

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            side_effect=lambda: terminal_size,
        ):
            await runtime.open()
            try:
                runtime.append_block(_block(source), kind="assistant")
                await asyncio.sleep(0.02)
                assert runtime.document.scrollback_line_count > 0

                with patch.object(
                    runtime.screen,
                    "clear_terminal_for_resize_replay",
                ) as clear, patch.object(
                    runtime.screen.application,
                    "print_text",
                    wraps=runtime.screen.application.print_text,
                ) as print_text:
                    terminal_size = Size(rows=14, columns=40)
                    runtime.viewport.observe_terminal_geometry(40, 14)
                    await asyncio.sleep(0.12)

                clear.assert_called_once_with()
                assert runtime.viewport._reflowed_geometry == (40, 14)
                chunks = [
                    "".join(text for _style, text in call.args[0])
                    for call in print_text.call_args_list
                ]
                visible = _document_text(runtime.document)
                assert chunks == [source + "\n"]
                assert visible == ""
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_resize_reflow_vt_transaction_wraps_erase_and_replay() -> None:
    stream = io.StringIO()
    terminal_size = Size(rows=8, columns=40)
    output = Vt100_Output(
        stream,
        lambda: terminal_size,
        term="xterm-256color",
        enable_cpr=False,
    )

    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=output)
        await runtime.open()
        try:
            runtime.append_block(
                _block("\n".join(f"entry {index:02d}" for index in range(60))),
                kind="assistant",
            )
            for _ in range(100):
                await asyncio.sleep(0.002)
                if (
                    runtime.document.scrollback_line_count > 0
                    and runtime.viewport.scrollback_task is None
                ):
                    break

            assert runtime.document.scrollback_line_count > 0
            stream.seek(0)
            stream.truncate(0)

            terminal_size = Size(rows=8, columns=24)
            runtime.viewport.observe_terminal_geometry(24, 8)
            for _ in range(200):
                await asyncio.sleep(0.002)
                if (
                    runtime.viewport._reflowed_geometry == (24, 8)
                    and runtime.viewport._scrollback_reflow_task is None
                ):
                    break

            payload = stream.getvalue()
            synchronized_begin = payload.find("\x1b[?2026h")
            synchronized_end = payload.find(
                "\x1b[?2026l",
                synchronized_begin + 1,
            )
            erased = payload.find("\x1b[J", synchronized_begin)
            cleared = payload.find(
                "\x1b[r\x1b[0m\x1b[H\x1b[2J\x1b[3J\x1b[H",
                synchronized_begin,
            )
            replayed = payload.find("entry 00", synchronized_begin)

            assert runtime.viewport._reflowed_geometry == (24, 8)
            assert -1 not in (
                synchronized_begin,
                erased,
                cleared,
                replayed,
                synchronized_end,
            )
            assert (
                synchronized_begin
                < erased
                < cleared
                < replayed
                < synchronized_end
            )
        finally:
            await runtime.close()


@pytest.mark.anyio
@pytest.mark.runtime_p0
@pytest.mark.runtime_frame
async def test_single_line_assistant_handoff_uses_synchronized_output() -> None:
    stream = io.StringIO()
    terminal_size = Size(rows=16, columns=40)
    output = Vt100_Output(
        stream,
        lambda: terminal_size,
        term="xterm-256color",
        enable_cpr=False,
    )

    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=output)
        session = create_tui_output_session(
            "",
            context=OUTPUT_SURFACE_CONTEXT,
            runtime=runtime,
            animate=True,
        )
        await runtime.open()
        try:
            runtime.set_execution_active(True)
            await runtime.begin_wait_status()
            await session.open()
            await session.activity.emit(ModelWaitRequested(
                surface_id=OUTPUT_SURFACE_CONTEXT.surface_id,
                turn_id=OUTPUT_SURFACE_CONTEXT.turn_id,
                revision=1,
                reason="initial",
            ))
            await session.activity.emit(AssistantBuffered(
                surface_id=OUTPUT_SURFACE_CONTEXT.surface_id,
                turn_id=OUTPUT_SURFACE_CONTEXT.turn_id,
                identity=RESPONSE_IDENTITY,
                item_id="item_single_line",
            ))
            await _render_next_frame(runtime)

            stream.seek(0)
            stream.truncate(0)
            await session.content.emit(AssistantTextDelta(
                "Done.",
                RESPONSE_IDENTITY,
                item_id="item_single_line",
            ))
            await session.content.emit(AssistantSegmentCompleted(
                RESPONSE_IDENTITY,
                item_id="item_single_line",
            ))
            await _render_next_frame(runtime)

            payload = stream.getvalue()
            synchronized_begin = payload.find("\x1b[?2026h")
            answer = payload.find("Done.", synchronized_begin)
            synchronized_end = payload.find("\x1b[?2026l", answer)

            assert -1 not in (
                synchronized_begin,
                answer,
                synchronized_end,
            )
            assert synchronized_begin < answer < synchronized_end
            assert payload.count("\x1b[?2026h") == 1
            assert payload.count("\x1b[?2026l") == 1
        finally:
            runtime.set_execution_active(False)
            await session.close()
            await runtime.close()


@pytest.mark.anyio
async def test_resize_rechecks_settled_geometry_before_reflow() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        terminal_size = Size(rows=8, columns=40)

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            side_effect=lambda: terminal_size,
        ):
            await runtime.open()
            try:
                runtime.append_block(
                    _block("\n".join(f"entry {index}" for index in range(60))),
                    kind="assistant",
                )
                await asyncio.sleep(0.02)

                with patch.object(
                    runtime.screen,
                    "clear_terminal_for_resize_replay",
                ) as clear:
                    terminal_size = Size(rows=8, columns=24)
                    runtime.viewport.observe_terminal_geometry(24, 8)
                    terminal_size = Size(rows=12, columns=30)
                    await asyncio.sleep(0.2)

                clear.assert_called_once_with()
                assert runtime.viewport._observed_geometry == (30, 12)
                assert runtime.viewport._reflowed_geometry == (30, 12)
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_resize_storm_replays_once_in_synchronized_output() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        terminal_size = Size(rows=8, columns=40)
        events: list[str] = []

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            side_effect=lambda: terminal_size,
        ):
            await runtime.open()
            try:
                runtime.append_block(
                    _block("\n".join(f"entry {index}" for index in range(60))),
                    kind="assistant",
                )
                await asyncio.sleep(0.02)

                @asynccontextmanager
                async def controlled_terminal(
                    render_cli_done: bool = False,
                ):
                    _ = render_cli_done
                    events.append("wait")
                    await asyncio.sleep(0)
                    events.append("acquired")
                    try:
                        yield
                    finally:
                        events.append("redraw")

                with (
                    patch(
                        "frontends.tui.core.viewport.in_terminal",
                        controlled_terminal,
                    ),
                    patch.object(
                        runtime.screen,
                        "begin_synchronized_output",
                        side_effect=lambda: events.append("begin") or True,
                    ) as begin,
                    patch.object(
                        runtime.screen,
                        "clear_terminal_for_resize_replay",
                        side_effect=lambda: events.append("clear"),
                    ) as clear,
                    patch.object(
                        runtime.screen,
                        "end_synchronized_output",
                        side_effect=lambda: events.append("end"),
                    ) as end,
                    patch.object(
                        runtime.screen.application,
                        "print_text",
                        side_effect=lambda _fragments: events.append("replay"),
                    ),
                    patch.object(
                        runtime.screen.application.renderer,
                        "clear",
                    ) as renderer_clear,
                ):
                    for width, height in (
                        (30, 9),
                        (22, 11),
                        (48, 14),
                        (36, 12),
                    ):
                        terminal_size = Size(rows=height, columns=width)
                        runtime.viewport.observe_terminal_geometry(width, height)

                    loop = asyncio.get_running_loop()
                    deadline = loop.time() + 1.0
                    while loop.time() < deadline:
                        reflow_task = runtime.viewport._scrollback_reflow_task
                        if clear.call_count and (
                            reflow_task is None or reflow_task.done()
                        ):
                            break
                        await asyncio.sleep(0.001)

                begin.assert_called_once_with()
                clear.assert_called_once_with()
                end.assert_called_once_with()
                renderer_clear.assert_not_called()
                assert events == [
                    "begin",
                    "wait",
                    "acquired",
                    "clear",
                    "replay",
                    "redraw",
                    "end",
                ]
                assert runtime.viewport._reflowed_geometry == (36, 12)
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_resize_reflow_releases_sync_when_terminal_wait_is_cancelled(
) -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        terminal_size = Size(rows=8, columns=40)
        waiting = asyncio.Event()
        blocked = asyncio.Event()
        events: list[str] = []

        @asynccontextmanager
        async def blocked_terminal(render_cli_done: bool = False):
            _ = render_cli_done
            events.append("wait")
            waiting.set()
            await blocked.wait()
            yield

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            side_effect=lambda: terminal_size,
        ):
            await runtime.open()
            try:
                runtime.append_block(
                    _block("\n".join(
                        f"entry {index}" for index in range(60)
                    )),
                    kind="assistant",
                )
                await asyncio.sleep(0.02)
                previous_position = runtime.document.scrollback_line_count
                assert previous_position > 0

                terminal_size = Size(rows=8, columns=24)
                runtime.viewport.observe_terminal_geometry(24, 8)
                runtime.viewport._cancel_scrollback_reflow()
                generation = runtime.viewport._reflow_generation

                with (
                    patch(
                        "frontends.tui.core.viewport.in_terminal",
                        blocked_terminal,
                    ),
                    patch.object(
                        runtime.viewport,
                        "_schedule_scrollback_reflow",
                    ),
                    patch.object(
                        runtime.screen,
                        "begin_synchronized_output",
                        side_effect=lambda: events.append("begin") or True,
                    ) as begin,
                    patch.object(
                        runtime.screen,
                        "end_synchronized_output",
                        side_effect=lambda: events.append("end"),
                    ) as end,
                    patch.object(
                        runtime.screen,
                        "clear_terminal_for_resize_replay",
                    ) as clear,
                ):
                    task = asyncio.create_task(runtime.viewport._reflow_scrollback(
                        (24, 8),
                        generation,
                    ))
                    await asyncio.wait_for(waiting.wait(), timeout=1.0)
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task

                begin.assert_called_once_with()
                end.assert_called_once_with()
                clear.assert_not_called()
                assert events == ["begin", "wait", "end"]
                assert runtime.document.scrollback_line_count == previous_position
                assert runtime.viewport._reflow_required is True
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_resize_invalidated_while_acquiring_terminal_keeps_position() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        terminal_size = {"value": Size(rows=8, columns=40)}

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            side_effect=lambda: terminal_size["value"],
        ):
            await runtime.open()
            try:
                runtime.append_block(
                    _block("\n".join(f"entry {index}" for index in range(60))),
                    kind="assistant",
                )
                await asyncio.sleep(0.02)

                previous_position = runtime.document.scrollback_line_count
                assert previous_position > 0

                terminal_size["value"] = Size(rows=8, columns=24)
                runtime.viewport.observe_terminal_geometry(24, 8)
                runtime.viewport._cancel_scrollback_reflow()
                generation = runtime.viewport._reflow_generation

                @asynccontextmanager
                async def resize_while_waiting(
                    render_cli_done: bool = False,
                ):
                    _ = render_cli_done
                    terminal_size["value"] = Size(rows=8, columns=40)
                    runtime.viewport.observe_terminal_geometry(40, 8)
                    yield

                with (
                    patch(
                        "frontends.tui.core.viewport.in_terminal",
                        resize_while_waiting,
                    ),
                    patch.object(
                        runtime.screen,
                        "begin_synchronized_output",
                        return_value=True,
                    ) as begin,
                    patch.object(
                        runtime.screen,
                        "end_synchronized_output",
                    ) as end,
                    patch.object(
                        runtime.screen,
                        "clear_terminal_for_resize_replay",
                    ) as clear,
                ):
                    await runtime.viewport._reflow_scrollback(
                        (24, 8),
                        generation,
                    )

                begin.assert_called_once_with()
                end.assert_called_once_with()
                clear.assert_not_called()
                assert runtime.document.scrollback_line_count == previous_position
                assert runtime.viewport._observed_geometry == (40, 8)
                assert runtime.viewport._reflowed_geometry == (40, 8)
                assert runtime.viewport._reflow_required is False
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_resize_replay_failure_restores_scrollback_position() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        terminal_size = Size(rows=8, columns=40)

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            side_effect=lambda: terminal_size,
        ):
            await runtime.open()
            try:
                runtime.append_block(
                    _block("\n".join(f"entry {index}" for index in range(60))),
                    kind="assistant",
                )
                await asyncio.sleep(0.02)

                previous_position = runtime.document.scrollback_line_count
                terminal_size = Size(rows=10, columns=24)
                runtime.viewport.observe_terminal_geometry(24, 10)
                runtime.viewport._cancel_scrollback_reflow()
                generation = runtime.viewport._reflow_generation

                @asynccontextmanager
                async def controlled_terminal(
                    render_cli_done: bool = False,
                ):
                    _ = render_cli_done
                    yield

                with (
                    patch(
                        "frontends.tui.core.viewport.in_terminal",
                        controlled_terminal,
                    ),
                    patch.object(
                        runtime.screen,
                        "begin_synchronized_output",
                        return_value=True,
                    ) as begin,
                    patch.object(
                        runtime.screen,
                        "end_synchronized_output",
                    ) as end,
                    patch.object(
                        runtime.screen.application,
                        "print_text",
                        side_effect=RuntimeError("replay failed"),
                    ),
                ):
                    with pytest.raises(RuntimeError, match="replay failed"):
                        await runtime.viewport._reflow_scrollback(
                            (24, 10),
                            generation,
                        )

                begin.assert_called_once_with()
                end.assert_called_once_with()
                assert runtime.document.scrollback_line_count == previous_position
                assert runtime.viewport._reflow_required is True
            finally:
                runtime.viewport._cancel_scrollback_reflow()
                await runtime.close()


@pytest.mark.anyio
async def test_resize_rechecks_geometry_reported_after_first_replay() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        terminal_size = Size(rows=8, columns=40)

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            side_effect=lambda: terminal_size,
        ):
            await runtime.open()
            try:
                runtime.append_block(
                    _block("\n".join(f"entry {index}" for index in range(60))),
                    kind="assistant",
                )
                await asyncio.sleep(0.02)

                with patch.object(
                    runtime.screen,
                    "clear_terminal_for_resize_replay",
                ) as clear:
                    terminal_size = Size(rows=8, columns=24)
                    runtime.viewport.observe_terminal_geometry(24, 8)
                    await asyncio.sleep(0.11)
                    clear.assert_called_once_with()

                    terminal_size = Size(rows=12, columns=30)
                    await asyncio.sleep(0.2)

                assert clear.call_count == 2
                assert runtime.viewport._observed_geometry == (30, 12)
                assert runtime.viewport._reflowed_geometry == (30, 12)
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_resize_during_stream_replays_final_stable_content() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        terminal_size = Size(rows=8, columns=40)
        stable_source = "\n".join(
            f"stable entry {index:02d}" for index in range(60)
        )
        stream_source = "\n".join(
            f"stream entry {index:02d}" for index in range(30)
        )

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            side_effect=lambda: terminal_size,
        ):
            await runtime.open()
            try:
                runtime.append_block(_block(stable_source), kind="assistant")
                await asyncio.sleep(0.02)
                runtime.set_active_renderable(
                    _block(stream_source),
                    kind="assistant",
                    raw_text=stream_source,
                    stream_continuation=True,
                )

                with patch.object(
                    runtime.screen,
                    "clear_terminal_for_resize_replay",
                ) as clear:
                    terminal_size = Size(rows=10, columns=24)
                    runtime.viewport.observe_terminal_geometry(24, 10)
                    await asyncio.sleep(0.12)

                    clear.assert_called_once_with()
                    assert runtime.viewport._resize_during_stream is True
                    await _render_next_frame(runtime)
                    assert runtime.screen._visible_height() == (
                        runtime.screen._natural_visible_height()
                    )

                    runtime.commit_active_renderable(
                        _block(stream_source),
                        raw_text=stream_source,
                    )
                    await asyncio.sleep(0.05)

                assert clear.call_count == 2
                assert runtime.viewport._resize_during_stream is False
                assert runtime.viewport._reflowed_geometry == (24, 10)
                transcript = _transcript_text(runtime.document)
                assert transcript.count("stream entry 00") == 1
                assert transcript.count("stream entry 29") == 1
                screen = await _render_next_frame(runtime)
                assert runtime.screen._visible_height() == (
                    runtime.screen._natural_visible_height()
                )
                assert runtime.screen.canvas_spacer not in (
                    screen.visible_windows_to_write_positions
                )
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_stream_commit_during_resize_reflows_only_final_geometry() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        terminal_size = Size(rows=8, columns=40)
        stable_source = "\n".join(
            f"stable entry {index:02d}" for index in range(60)
        )
        stream_source = "$ command\n" + "stream output " * 12

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            side_effect=lambda: terminal_size,
        ):
            await runtime.open()
            try:
                runtime.append_block(_block(stable_source), kind="assistant")
                await asyncio.sleep(0.02)
                assert runtime.document.scrollback_line_count > 0

                runtime.toggle_transcript_overlay()
                runtime.set_active_renderable(
                    _block("running"),
                    kind="operation",
                    transcript_block=_block(stream_source),
                )

                with patch.object(
                    runtime.screen,
                    "clear_terminal_for_resize_replay",
                ) as clear:
                    for width, height in ((30, 9), (22, 11), (36, 12)):
                        terminal_size = Size(rows=height, columns=width)
                        runtime.viewport.observe_terminal_geometry(width, height)

                    runtime.commit_active_renderable(
                        _block("completed"),
                        transcript_block=_block(stream_source),
                    )
                    await asyncio.sleep(0.12)
                    clear.assert_not_called()

                    runtime.toggle_transcript_overlay()
                    await asyncio.sleep(0.12)

                clear.assert_called_once_with()
                assert runtime.viewport._reflowed_geometry == (36, 12)
                transcript = _transcript_text(runtime.document)
                assert transcript.count("$ command") == 1
                assert transcript.count("stream output") == 12
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_modal_return_refreshes_terminal_geometry() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        terminal = SimpleNamespace(size=Size(rows=10, columns=40))

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            side_effect=lambda: terminal.size,
        ):
            await runtime.open()
            try:
                async def external_program() -> str:
                    terminal.size = Size(rows=16, columns=32)
                    return "done"

                assert await runtime.run_modal(external_program) == "done"
                assert runtime.viewport._observed_geometry == (32, 16)
                await asyncio.sleep(0.12)
                assert runtime.viewport._reflowed_geometry == (32, 16)
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_scrollback_reflow_uses_configured_line_limit() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        runtime.configure_scrollback_reflow_line_limit(7)
        terminal_size = Size(rows=8, columns=40)

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            side_effect=lambda: terminal_size,
        ):
            await runtime.open()
            try:
                runtime.append_block(
                    _block("\n".join(f"entry {index}" for index in range(60))),
                    kind="assistant",
                )
                await asyncio.sleep(0.02)

                with patch.object(
                    runtime.document,
                    "rewind_scrollback",
                    wraps=runtime.document.rewind_scrollback,
                ) as rewind:
                    terminal_size = Size(rows=12, columns=40)
                    runtime.viewport.observe_terminal_geometry(40, 12)
                    await asyncio.sleep(0.12)

                rewind.assert_called_once_with(max_line_count=7)
            finally:
                await runtime.close()


def test_scrollback_reflow_keeps_clear_boundary_and_caps_replay() -> None:
    document = TuiDocument()
    document.append_block(
        _block("\n".join(f"old {index}" for index in range(20))),
        kind="assistant",
    )
    document.commit_scrollback_prefix(20, expected_start=0)
    document.clear_visible_prefix()
    document.append_block(
        _block("\n".join(f"new {index}" for index in range(20))),
        kind="assistant",
    )
    document.commit_scrollback_prefix(21, expected_start=21)

    document.rewind_scrollback(max_line_count=5)

    assert document.scrollback_line_count == len(document._stable_lines) - 5
    assert document.scrollback_line_count >= document.cleared_line_count
    assert "old 19" not in "".join(
        text for line in document.visible_stable_lines() for _style, text in line
    )


@pytest.mark.anyio
async def test_queued_scrollback_rechecks_foreground_state_before_flushing(
) -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=10, columns=40),
        ):
            await runtime.open()
            try:
                runtime.set_foreground_active(True)
                render_count = runtime.screen.application.render_counter
                for index in range(6):
                    runtime.append_block(
                        _block(f"block {index}\n" + "line\n" * 3),
                        kind="operation",
                    )

                for _ in range(20):
                    await asyncio.sleep(0)
                    if runtime.screen.application.render_counter > render_count:
                        break

                runtime.set_foreground_active(False)
                assert runtime.viewport.scrollback_task is not None
                runtime.set_foreground_active(True)

                await asyncio.sleep(0.02)

                assert runtime.document.scrollback_line_count == 0

                runtime.set_foreground_active(False)
                await asyncio.sleep(0.02)

                assert runtime.document.scrollback_line_count > 0
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_stable_commit_waits_for_render_before_scrollback() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=10, columns=40),
        ):
            await runtime.open()
            try:
                block = _block("line\n" * 20)
                runtime.set_active_renderable(block, kind="assistant")

                for _ in range(20):
                    await asyncio.sleep(0)
                    if runtime.screen.application.render_counter >= 2:
                        break

                runtime.commit_active_renderable(block)

                assert runtime.viewport.scrollback_task is None

                for _ in range(50):
                    await asyncio.sleep(0.002)
                    if runtime.document.scrollback_line_count > 0:
                        break

                assert runtime.document.scrollback_line_count > 0
            finally:
                await runtime.close()
