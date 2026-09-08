# -*- coding: utf-8 -*-

"""验证补全选择在输入编辑、关闭与历史恢复中的状态。"""


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


def rendered_input_line(runtime: TuiRuntime) -> str:
    """返回最近一次渲染中的首行输入文本。"""
    return rendered_input_lines(runtime)[0]


def rendered_input_lines(runtime: TuiRuntime) -> list[str]:
    """返回最近一次渲染中的完整输入区域文本。"""
    screen = runtime.screen.application.renderer.last_rendered_screen
    prompt_position = screen.visible_windows_to_write_positions[
        runtime.screen.input_prompt_window
    ]
    input_position = screen.visible_windows_to_write_positions[
        runtime.screen.input.window
    ]
    end_column = input_position.xpos + input_position.width
    return [
        "".join(
            screen.data_buffer[row][column].char
            for column in range(prompt_position.xpos, end_column)
        ).rstrip()
        for row in range(
            input_position.ypos,
            input_position.ypos + input_position.height,
        )
    ]


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
async def test_complete_skill_remains_selected_until_it_is_accepted() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        runtime.input_model.set_skills((skill_spec("alpha"),))

        await runtime.open()
        try:
            pipe_input.send_text("$alpha")
            await wait_for_completion(runtime)

            buffer = runtime.screen.input.buffer
            assert buffer.text == "$alpha"
            assert buffer.complete_state is not None
            assert buffer.complete_state.current_completion.text == "$alpha "

            pipe_input.send_text("\t")
            await wait_for_input_text(runtime, "$alpha ")

            assert buffer.complete_state is None
            assert runtime.submissions.message_queue.empty()
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_selected_skill_stays_dismissed_while_its_anchor_remains() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        runtime.input_model.set_skills((skill_spec("alpha"),))

        await runtime.open()
        try:
            pipe_input.send_text("$alph")
            await wait_for_completion(runtime)
            pipe_input.send_text("\r")
            await wait_for_input_text(runtime, "$alpha ")

            buffer = runtime.screen.input.buffer
            assert buffer.complete_state is None

            for key, position in (
                ("\x1b[D", 6),
                ("\x1b[D", 5),
                ("\x1b[C", 6),
                ("\x1b[C", 7),
            ):
                pipe_input.send_text(key)
                await wait_for_cursor_position(runtime, position)
                assert runtime.input_model.completion_menu_completions(
                    buffer.document
                ) is None
                assert runtime.screen._footer_visible()

            pipe_input.send_text("\x7f")
            await wait_for_input_text(runtime, "$alpha")

            assert buffer.complete_state is None
            assert not runtime.screen._completion_visible()
            assert runtime.screen._footer_visible()

            pipe_input.send_text("\x1b[D\x1b[D")
            await wait_for_cursor_position(runtime, 4)
            pipe_input.send_text("\x7f")
            await wait_for_input_text(runtime, "$alha")

            assert buffer.complete_state is None
            assert runtime.input_model.completion_menu_completions(
                buffer.document
            ) is None

            pipe_input.send_text("\x1b[3~")
            await wait_for_input_text(runtime, "$ala")

            assert buffer.complete_state is None
            assert runtime.input_model.completion_menu_completions(
                buffer.document
            ) is None

            pipe_input.send_text("z")
            await wait_for_input_text(runtime, "$alza")

            assert buffer.complete_state is None
            assert runtime.input_model.completion_menu_completions(
                buffer.document
            ) is None

            screen = await render_next_frame(runtime)
            assert runtime.screen.footer_window in (
                screen.visible_windows_to_write_positions
            )

            buffer.cursor_position = 1
            pipe_input.send_text("\x7f$a")
            await wait_for_input_text(runtime, "$aalza")
            await wait_for_completion(runtime)

            assert runtime.input_model.completion_menu_completions(
                buffer.document
            ) is not None
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_selected_skill_tracks_edits_outside_its_token() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        runtime.input_model.set_skills((
            skill_spec("alpha"),
            skill_spec("beta"),
        ))

        await runtime.open()
        try:
            runtime.replace_input_text("$alpha ", selected_skill=True)
            buffer = runtime.screen.input.buffer

            pipe_input.send_text("notes ")
            await wait_for_input_text(runtime, "$alpha notes ")
            buffer.cursor_position = 0
            pipe_input.send_text("ask ")
            await wait_for_input_text(runtime, "ask $alpha notes ")

            buffer.cursor_position = len("ask $alp")
            assert runtime.input_model.completion_menu_completions(
                buffer.document
            ) is None

            buffer.cursor_position = len(buffer.text)
            pipe_input.send_text("$b")
            await wait_for_input_text(runtime, "ask $alpha notes $b")
            await wait_for_completion(runtime)

            completions = runtime.input_model.completion_menu_completions(
                buffer.document
            )
            assert completions is not None
            assert [completion.text for completion in completions] == [
                "$beta "
            ]
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_unknown_skill_renders_non_selectable_empty_state() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        runtime.input_model.set_skills((skill_spec("alpha"),))

        await runtime.open()
        try:
            pipe_input.send_text("$zzz")
            await wait_for_input_text(runtime, "$zzz")
            runtime.screen.application.invalidate()
            await asyncio.sleep(0)

            assert runtime.screen.input.buffer.complete_state is None
            assert runtime.screen._completion_fallback_fragments() == [
                ("class:completion-menu.empty", "  no matches"),
            ]
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_backspace_preserves_selected_skill_without_async_restart() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        runtime.input_model.set_skills((
            skill_spec("alpha"),
            skill_spec("alphabet"),
        ))

        await runtime.open()
        try:
            pipe_input.send_text("$alph")
            await wait_for_completion(runtime)

            buffer = runtime.screen.input.buffer
            runtime.input_model._select_completion(buffer, 1)
            assert buffer.complete_state is not None
            assert buffer.complete_state.current_completion.text == "$alphabet "

            with patch.object(
                buffer,
                "start_completion",
                wraps=buffer.start_completion,
            ) as start_completion:
                pipe_input.send_text("\x7f\x7f")
                await wait_for_input_text(runtime, "$al")

            start_completion.assert_not_called()
            assert buffer.complete_state is not None
            assert buffer.complete_state.current_completion.text == "$alphabet "
        finally:
            await runtime.close()


def test_dismissed_slash_menu_reopens_after_editing() -> None:
    runtime = TuiRuntime()
    buffer = runtime.screen.input.buffer
    buffer.document = Document("/aaa", cursor_position=4)

    runtime.input_model.dismiss_completion_menu(buffer)
    assert not runtime.screen._completion_visible()

    buffer.document = Document("/aaax", cursor_position=5)
    buffer.document = Document("/aaa", cursor_position=4)

    assert runtime.screen._completion_fallback_visible()


@pytest.mark.anyio
async def test_idle_plain_query_tab_submits_like_enter() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        await runtime.open()
        try:
            buffer = runtime.screen.input.buffer
            pipe_input.send_text("h")
            await wait_for_input_text(runtime, "h")
            assert buffer.suggestion is None

            pipe_input.send_text("\t")
            submission = await asyncio.wait_for(
                runtime.submissions.message_queue.get(),
                timeout=1,
            )

            assert submission.value == "h"
            assert runtime.submissions.surface_submission_pending is False
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_idle_shell_tab_keeps_editing_draft() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        await runtime.open()
        try:
            pipe_input.send_text("!echo hello\t")
            await wait_for_input_text(runtime, "echo hello    ")

            assert runtime.input_model.shell_mode
            assert runtime.submissions.message_queue.empty()
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_backspace_reopens_completion_menu() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        await runtime.open()
        try:
            pipe_input.send_text("/sX")
            await wait_for_input_text(runtime, "/sX")

            buffer = runtime.screen.input.buffer
            assert buffer.complete_state is None

            pipe_input.send_text("\x7f")
            await wait_for_input_text(runtime, "/s")
            await wait_for_completion(runtime)

            assert buffer.complete_state is not None
            assert [
                completion.display_text
                for completion in buffer.complete_state.completions
            ] == ["/stop", "/skills", "/shutdown"]
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_dismissed_skill_menu_stays_closed_with_cursor_motion() -> None:
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

            buffer = runtime.screen.input.buffer
            runtime.input_model.dismiss_completion_menu(buffer)
            await wait_for_no_completion(runtime)

            pipe_input.send_text("\x1b[D")
            await wait_for_cursor_position(runtime, 3)
            assert runtime.input_model.completion_menu_completions(
                buffer.document
            ) is None

            pipe_input.send_text("\x1b[C")
            await wait_for_cursor_position(runtime, 4)
            assert runtime.input_model.completion_menu_completions(
                buffer.document
            ) is None
        finally:
            await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("initial", "move", "erase", "target_cursor"),
    (
        pytest.param(" !", "\x1b[D", "\x7f", 1, id="backspace"),
        pytest.param(" !", "\x01", "\x1b[3~", 0, id="delete"),
        pytest.param("x !", "\x1b[D", "\x17", 2, id="ctrl-w"),
    ),
)
async def test_destructive_edit_promotes_revealed_shell_prefix(
    initial: str,
    move: str,
    erase: str,
    target_cursor: int,
) -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        await runtime.open()
        try:
            pipe_input.send_text(initial)
            await wait_for_input_text(runtime, initial)

            pipe_input.send_text(move)
            for _ in range(1000):
                if runtime.screen.input.buffer.cursor_position == target_cursor:
                    break
                await asyncio.sleep(0.001)
            else:
                raise AssertionError("cursor did not move before shell prefix")

            pipe_input.send_text(erase)
            for _ in range(1000):
                if runtime.input_model.shell_mode:
                    break
                await asyncio.sleep(0.001)
            else:
                raise AssertionError("shell prefix was not promoted")

            buffer = runtime.screen.input.buffer
            assert buffer.text == ""
            assert buffer.cursor_position == 0

            await render_next_frame(runtime)
            assert rendered_input_line(runtime) == "!"
            assert runtime.screen.input.window.get_line_prefix is None
            assert runtime.screen._input_prompt_fragments() == [
                ("class:shell-escape", "!")
            ]
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_control_up_restores_structured_shell_history() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        await runtime.open()
        try:
            read = asyncio.create_task(
                runtime.read_message(PromptContext(model="test"))
            )
            pipe_input.send_text("!adb devices\r")

            assert await asyncio.wait_for(read, timeout=1.0) == "! adb devices"
            await wait_for_input_text(runtime, "")

            pipe_input.send_text("\x1b[1;5A")
            await wait_for_input_text(runtime, "adb devices")

            assert runtime.input_model.shell_mode
            assert runtime.screen.input.buffer.cursor_position == len(
                "adb devices"
            )

            await render_next_frame(runtime)
            assert rendered_input_line(runtime) == "! adb devices"

            pipe_input.send_text("\x1b[1;5B")
            await wait_for_input_text(runtime, "")

            assert not runtime.input_model.shell_mode
        finally:
            await runtime.close()
