# -*- coding: utf-8 -*-

"""验证补全命令执行后的前台活动与布局恢复。"""


import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from prompt_toolkit.completion import Completion
from prompt_toolkit.data_structures import Size
from prompt_toolkit.document import Document
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.utils import get_cwidth
from agent.application.approvals.coordinator import ApprovalCoordinator
from frontends.terminal.capabilities import (
    TerminalCapabilities,
    TerminalTheme,
)
from frontends.terminal.color_support import (
    TerminalColorLevel,
    TerminalColorSupport,
)
from frontends.terminal.identity import (
    TerminalIdentity,
    TerminalKind,
)
from infrastructure.skills import SkillSpec
from frontends.interaction.contracts import PromptContext
from frontends.tui.adapters.output import TuiOutputControl
from frontends.tui.core.models import (
    FragmentBlock,
    MenuOption,
    MenuRequest,
)
from frontends.tui.core.render import fragments_text
from frontends.tui.core.runtime import TuiRuntime
from frontends.tui.core.styles import text_block
from frontends.tui.core.token_menu import (
    TokenCompletionMenuControl,
    TokenMenuItem,
    TokenMenuSnapshot,
    token_menu_display_height,
)
from frontends.tui.prompting.skills import (
    skill_display_text,
    skill_meta_text,
    skill_completions,
    skill_query_token,
)
from frontends.tui.session.barriers import TuiForegroundTasks


async def render_next_frame(runtime: TuiRuntime):
    """触发渲染并返回完成后的屏幕。"""
    previous_revision = runtime.screen.application.render_counter
    runtime.invalidate()
    for _ in range(20):
        await asyncio.sleep(0)
        if runtime.screen.application.render_counter > previous_revision:
            return runtime.screen.application.renderer.last_rendered_screen
    raise AssertionError("next frame was not rendered")


@pytest.mark.anyio
@pytest.mark.parametrize(
    "command",
    (
        "/helix-link",
        "/helix-stop",
        "/fork",
        "/compact",
        "/mcp stop",
        "/mcp status",
    ),
)
@pytest.mark.parametrize("prior_line_count", (0, 20))
async def test_slash_command_result_uses_current_layout(
    command: str,
    prior_line_count: int,
) -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=12, columns=40),
        ):
            await runtime.open()
            try:
                if prior_line_count:
                    runtime.append_block(
                        FragmentBlock(((
                            "",
                            "\n".join(
                                f"prior line {index}"
                                for index in range(prior_line_count)
                            ),
                        ),)),
                        kind="assistant",
                    )
                    await render_next_frame(runtime)

                read_task = asyncio.create_task(runtime.read_message(
                    PromptContext(model="test"),
                ))

                buffer = runtime.screen.input.buffer
                buffer.document = Document("/", cursor_position=1)
                runtime.input_model.refresh_completion_menu(buffer)
                assert buffer.complete_state is not None
                await render_next_frame(runtime)

                buffer.insert_text(command[1:])
                assert buffer.text == command
                screen = await render_next_frame(runtime)
                command_input = screen.visible_windows_to_write_positions[
                    runtime.screen.input.window
                ]
                command_input_row = (
                    12
                    - runtime.screen._visible_height()
                    + command_input.ypos
                )

                pipe_input.send_text("\r")
                assert await read_task == command

                runtime.begin_command_layout()
                render_revision = runtime.screen.application.render_counter
                runtime.append_block(
                    FragmentBlock((("", "■ command completed"),)),
                    kind="operation",
                )
                runtime.discard_pending_submission()
                runtime.finish_command_layout()

                screen = await render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions
                input_position = positions[runtime.screen.input.window]
                rows = {
                    row: "".join(
                        cells[column].char for column in sorted(cells)
                    ).rstrip()
                    for row, cells in screen.data_buffer.items()
                }
                result_row = next(
                    row
                    for row, text in rows.items()
                    if "command completed" in text
                )

                assert input_position.ypos - result_row == 3
                final_input_row = (
                    12
                    - runtime.screen._visible_height()
                    + input_position.ypos
                )
                assert final_input_row == command_input_row
                assert all(
                    not rows.get(row)
                    for row in range(result_row + 1, input_position.ypos)
                )
                assert runtime.screen.canvas_spacer not in positions
                assert not runtime.command_layout_pending

                for _ in range(5):
                    await asyncio.sleep(0)
                assert runtime.screen.application.render_counter > (
                    render_revision
                )
                assert runtime.document.scrollback_line_count == (
                    runtime.document.stable_line_count
                )
                assert "command completed" in fragments_text(
                    runtime.document.all_fragments(width=40)
                )
            finally:
                await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "command",
    (
        "/compact",
        "/fork",
        "/helix-link",
        "/helix-stop",
        "/mcp stop",
    ),
)
@pytest.mark.parametrize("terminal_rows", (12, 24))
async def test_foreground_command_uses_current_activity_layout(
    command: str,
    terminal_rows: int,
) -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        release = asyncio.Event()
        started = asyncio.Event()
        running_row: list[int] = []
        foreground = TuiForegroundTasks(
            runtime,
            SimpleNamespace(await_cleanup=lambda awaitable: awaitable),
        )

        async def operation() -> str:
            await runtime.begin_operation_status(
                lambda: {"summary": "Operation running"},
            )
            started.set()
            await release.wait()
            return "ready"

        async def capture_running_frame() -> None:
            await started.wait()
            try:
                screen = await render_next_frame(runtime)
                position = screen.visible_windows_to_write_positions[
                    runtime.screen.input.window
                ]
                running_row.append(
                    terminal_rows
                    - runtime.screen._visible_height()
                    + position.ypos
                )
            finally:
                release.set()

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=terminal_rows, columns=80),
        ):
            await runtime.open()
            observer = None
            try:
                screen = await render_next_frame(runtime)
                position = screen.visible_windows_to_write_positions[
                    runtime.screen.input.window
                ]
                idle_row = (
                    terminal_rows
                    - runtime.screen._visible_height()
                    + position.ypos
                )

                read_task = asyncio.create_task(runtime.read_message(
                    PromptContext(model="test"),
                ))
                buffer = runtime.screen.input.buffer
                buffer.document = Document("/", cursor_position=1)
                runtime.input_model.refresh_completion_menu(buffer)
                assert buffer.complete_state is not None
                await render_next_frame(runtime)
                buffer.insert_text(command[1:])
                assert buffer.text == command
                screen = await render_next_frame(runtime)
                position = screen.visible_windows_to_write_positions[
                    runtime.screen.input.window
                ]
                completion_row = (
                    terminal_rows
                    - runtime.screen._visible_height()
                    + position.ypos
                )

                pipe_input.send_text("\r")
                assert await read_task == command
                runtime.begin_command_layout()
                foreground.start(
                    "operation",
                    operation,
                    activity_kind="operation",
                    on_succeeded=lambda result: runtime.append_block(
                        text_block(f"Operation {result}"),
                        kind="operation",
                    ),
                )
                observer = asyncio.create_task(capture_running_frame())
                await foreground.wait()
                await observer

                screen = await render_next_frame(runtime)
                position = screen.visible_windows_to_write_positions[
                    runtime.screen.input.window
                ]
                final_row = (
                    terminal_rows
                    - runtime.screen._visible_height()
                    + position.ypos
                )

                assert completion_row == idle_row
                assert running_row == [idle_row]
                assert final_row == idle_row
                assert not runtime.command_layout_pending
            finally:
                release.set()
                if observer is not None:
                    await observer
                await runtime.close()


@pytest.mark.anyio
async def test_stream_command_result_uses_natural_layout_after_turn() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=12, columns=40),
        ):
            await runtime.open()
            try:
                handled: list[str] = []

                def handle_stream_command(value: str) -> bool:
                    handled.append(value)
                    return True

                runtime.bind_stream_command_handler(handle_stream_command)
                runtime.set_execution_active(True)
                runtime.set_active_renderable(
                    FragmentBlock((("", "streaming answer"),)),
                    kind="assistant",
                )

                buffer = runtime.screen.input.buffer
                buffer.document = Document("/", cursor_position=1)
                runtime.input_model.refresh_completion_menu(buffer)
                assert buffer.complete_state is not None
                await render_next_frame(runtime)
                buffer.insert_text("helix-link")
                pipe_input.send_text("\r")

                loop = asyncio.get_running_loop()
                deadline = loop.time() + 1.0
                while not handled and loop.time() < deadline:
                    await asyncio.sleep(0.001)
                assert handled == ["/helix-link"]

                runtime.queue_background_block(
                    FragmentBlock((("", "■ Helix MCP ready"),)),
                )
                runtime.commit_active_renderable(
                    FragmentBlock((("", "streaming answer\nfinished"),)),
                )
                runtime.set_execution_active(False)

                screen = await render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions
                input_position = positions[runtime.screen.input.window]
                rows = {
                    row: "".join(
                        cells[column].char for column in sorted(cells)
                    ).rstrip()
                    for row, cells in screen.data_buffer.items()
                }
                result_row = next(
                    row
                    for row, text in rows.items()
                    if "Helix MCP ready" in text
                )

                assert input_position.ypos - result_row == 3
                assert runtime.screen._visible_height() == (
                    runtime.screen._natural_visible_height()
                )
            finally:
                runtime.set_execution_active(False)
                await runtime.close()
