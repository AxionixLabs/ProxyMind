# -*- coding: utf-8 -*-

"""验证 skill completion 的匹配、导航与光标边界。"""


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


async def wait_for_completion(runtime: TuiRuntime) -> None:
    """等待当前输入对应的异步补全结果就绪。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 1.0
    while loop.time() < deadline:
        state = runtime.screen.input.buffer.complete_state
        if state is not None and state.completions:
            return
        await asyncio.sleep(0.001)
    raise AssertionError("completion did not become ready")


async def wait_for_no_completion(runtime: TuiRuntime) -> None:
    """等待当前输入的补全菜单收起。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 1.0
    while loop.time() < deadline:
        if runtime.screen.input.buffer.complete_state is None:
            return
        await asyncio.sleep(0.001)
    raise AssertionError("completion did not close")


async def wait_for_input_text(runtime: TuiRuntime, text: str) -> None:
    """等待管道输入被主输入框完整消费。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 1.0
    while loop.time() < deadline:
        if runtime.screen.input.buffer.text == text:
            return
        await asyncio.sleep(0.001)
    raise AssertionError(f"input text did not become {text!r}")


async def wait_for_cursor_position(runtime: TuiRuntime, position: int) -> None:
    """等待输入光标移动到指定位置。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 1.0
    while loop.time() < deadline:
        if runtime.screen.input.buffer.cursor_position == position:
            return
        await asyncio.sleep(0.001)
    raise AssertionError(f"input cursor did not move to {position}")


async def render_next_frame(runtime: TuiRuntime):
    """触发渲染并返回完成后的屏幕。"""
    previous_revision = runtime.screen.application.render_counter
    runtime.invalidate()
    for _ in range(20):
        await asyncio.sleep(0)
        if runtime.screen.application.render_counter > previous_revision:
            return runtime.screen.application.renderer.last_rendered_screen
    raise AssertionError("next frame was not rendered")


def skill_spec(name: str, description: str | None = None) -> SkillSpec:
    """创建输入补全测试使用的 skill 描述。"""
    entry = Path(f"{name}/SKILL.md")
    return SkillSpec(
        name=name,
        description=description or f"Use {name}",
        source="test",
        root=entry.parent,
        entry=entry,
    )


@pytest.mark.anyio
async def test_skill_prefix_selects_first_match_without_rewriting_input() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        runtime.input_model.set_skills((skill_spec("alpha"), skill_spec("beta")))

        await runtime.open()
        try:
            pipe_input.send_text("$")
            await wait_for_completion(runtime)

            buffer = runtime.screen.input.buffer
            assert buffer.text == "$"
            assert buffer.complete_state is not None
            assert buffer.complete_state.complete_index == 0
            assert buffer.complete_state.current_completion.text == "$alpha "

            runtime.screen.application.invalidate()
            await asyncio.sleep(0)

            screen = runtime.screen.application.renderer.last_rendered_screen
            menu_window = runtime.screen.completion_menu
            menu_position = screen.visible_windows_to_write_positions[
                menu_window
            ]
            assert "class:token-menu.skill.current" in (
                screen.data_buffer[menu_position.ypos][
                    menu_position.xpos + 2
                ].style
            )
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_skill_menu_sorts_empty_query_by_name() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        runtime.input_model.set_skills((
            skill_spec("zeta"),
            skill_spec("alpha"),
            skill_spec("beta"),
        ))

        await runtime.open()
        try:
            pipe_input.send_text("$")
            await wait_for_completion(runtime)

            buffer = runtime.screen.input.buffer
            assert buffer.complete_state is not None
            assert [
                completion.text
                for completion in buffer.complete_state.completions
            ] == ["$alpha ", "$beta ", "$zeta "]
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_skill_menu_ctrl_p_and_ctrl_n_wrap_selection() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        runtime.input_model.set_skills((
            skill_spec("alpha"),
            skill_spec("beta"),
        ))

        await runtime.open()
        try:
            pipe_input.send_text("$")
            await wait_for_completion(runtime)

            buffer = runtime.screen.input.buffer
            assert buffer.complete_state is not None
            assert buffer.complete_state.current_completion.text == "$alpha "

            pipe_input.send_text("\x10")
            for _ in range(100):
                if buffer.complete_state.current_completion.text == "$beta ":
                    break
                await asyncio.sleep(0.001)
            assert buffer.complete_state.current_completion.text == "$beta "

            pipe_input.send_text("\x0e")
            for _ in range(100):
                if buffer.complete_state.current_completion.text == "$alpha ":
                    break
                await asyncio.sleep(0.001)
            assert buffer.complete_state.current_completion.text == "$alpha "
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_shift_tab_routes_only_to_open_completion_popup() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        runtime.input_model.set_skills((
            skill_spec("alpha"),
            skill_spec("beta"),
        ))

        await runtime.open()
        try:
            pipe_input.send_text("plain draft")
            await wait_for_input_text(runtime, "plain draft")
            pipe_input.send_text("\x1b[Z")
            await asyncio.sleep(0.05)

            buffer = runtime.screen.input.buffer
            assert buffer.text == "plain draft"
            assert buffer.complete_state is None

            buffer.text = ""
            pipe_input.send_text("$")
            await wait_for_input_text(runtime, "$")
            await wait_for_completion(runtime)
            assert buffer.complete_state is not None
            assert buffer.complete_state.current_completion.text == "$alpha "

            pipe_input.send_text("\x1b[Z")
            for _ in range(100):
                if buffer.complete_state.current_completion.text == "$beta ":
                    break
                await asyncio.sleep(0.001)

            assert buffer.text == "$"
            assert buffer.complete_state.current_completion.text == "$beta "
            assert runtime.submissions.message_queue.empty()
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_skill_menu_accepts_non_contiguous_query_matches() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        runtime.input_model.set_skills((
            skill_spec("generate-client"),
            skill_spec("git-commit"),
            skill_spec("review"),
        ))

        await runtime.open()
        try:
            pipe_input.send_text("$gc")
            await wait_for_completion(runtime)

            buffer = runtime.screen.input.buffer
            assert buffer.complete_state is not None
            assert [
                completion.text
                for completion in buffer.complete_state.completions
            ] == ["$git-commit ", "$generate-client "]
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_skill_menu_survives_left_and_right_cursor_motion() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        runtime.input_model.set_skills((
            skill_spec("alpha"),
            skill_spec("alphabet"),
        ))

        await runtime.open()
        try:
            pipe_input.send_text("$alp")
            await wait_for_completion(runtime)

            pipe_input.send_text("\x1b[D")
            await wait_for_cursor_position(runtime, 3)

            buffer = runtime.screen.input.buffer
            assert buffer.complete_state is not None
            assert [
                completion.text
                for completion in buffer.complete_state.completions
            ] == ["$alpha ", "$alphabet "]

            pipe_input.send_text("\x1b[C")
            await wait_for_cursor_position(runtime, 4)

            assert buffer.complete_state is not None
            assert [
                completion.text
                for completion in buffer.complete_state.completions
            ] == ["$alpha ", "$alphabet "]
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_skill_menu_reopens_on_bare_token_after_cursor_up() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        runtime.input_model.set_skills((skill_spec("browser"),))

        await runtime.open()
        try:
            pipe_input.send_text("\x1b[200~$\n$\n$\x1b[201~")
            await wait_for_input_text(runtime, "$\n$\n$")
            await wait_for_completion(runtime)

            pipe_input.send_text("\r")
            await wait_for_input_text(runtime, "$\n$\n$browser ")
            await wait_for_no_completion(runtime)

            pipe_input.send_text("\x1b[A")
            await wait_for_cursor_position(runtime, 3)
            await wait_for_completion(runtime)

            buffer = runtime.screen.input.buffer
            assert buffer.document.text_before_cursor == "$\n$"
            assert buffer.complete_state is not None
            assert [
                completion.text
                for completion in buffer.complete_state.completions
            ] == ["$browser "]
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_skill_menu_closes_when_cursor_leaves_token_left_edge() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        runtime.input_model.set_skills((
            skill_spec("alpha"),
            skill_spec("alphabet"),
        ))

        await runtime.open()
        try:
            pipe_input.send_text("$alp")
            await wait_for_completion(runtime)

            pipe_input.send_text("\x1b[D\x1b[D\x1b[D\x1b[D")
            await wait_for_cursor_position(runtime, 0)
            await wait_for_no_completion(runtime)

            pipe_input.send_text("\x1b[C")
            await wait_for_cursor_position(runtime, 1)
            await wait_for_completion(runtime)

            buffer = runtime.screen.input.buffer
            assert buffer.complete_state is not None
            assert [
                completion.text
                for completion in buffer.complete_state.completions
            ] == ["$alpha ", "$alphabet "]
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_skill_menu_keeps_all_matches_beyond_visible_height() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        runtime.input_model.set_skills(tuple(
            skill_spec(f"skill-{index:02d}")
            for index in range(1, 13)
        ))

        runtime.screen.application.output.get_size = lambda: Size(
            rows=10,
            columns=100,
        )

        await runtime.open()
        try:
            pipe_input.send_text("$")
            await wait_for_completion(runtime)

            buffer = runtime.screen.input.buffer
            assert buffer.complete_state is not None
            assert len(buffer.complete_state.completions) == 12
            assert runtime.screen._completion_height() == 6
            assert runtime.screen._bottom_pane_top_inset_height() == 1
            assert runtime.screen._input_surface_height() == 3
            assert not runtime.screen.completion_menu.right_margins

            runtime.input_model._select_completion(buffer, 11)

            assert buffer.complete_state.current_completion.text == "$skill-12 "
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_backspacing_skill_query_does_not_move_input() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        runtime.input_model.set_skills(tuple(
            skill_spec(f"skill-{index:02d}")
            for index in range(1, 13)
        ))

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=12, columns=40),
        ):
            await runtime.open()
            try:
                pipe_input.send_text("$")
                await wait_for_completion(runtime)
                screen = await render_next_frame(runtime)
                opened = screen.visible_windows_to_write_positions[
                    runtime.screen.input.window
                ].ypos

                query = "$skill-01"
                pipe_input.send_text(query[1:])
                await wait_for_input_text(runtime, query)

                for remaining_length in range(len(query) - 1, -1, -1):
                    pipe_input.send_text("\x7f")
                    expected = query[:remaining_length]
                    await wait_for_input_text(runtime, expected)
                    screen = await render_next_frame(runtime)
                    positions = screen.visible_windows_to_write_positions

                    assert (
                        positions[runtime.screen.input.window].ypos
                        == opened
                    )
                    assert runtime.screen.canvas_spacer not in positions
            finally:
                await runtime.close()
