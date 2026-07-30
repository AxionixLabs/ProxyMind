# -*- coding: utf-8 -*-

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest
from prompt_toolkit.completion import Completion
from prompt_toolkit.data_structures import Size
from prompt_toolkit.document import Document
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from mind_core.skills import SkillSpec
from mind_app.tui.core.runtime import TuiRuntime


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


async def wait_for_suggestion(runtime: TuiRuntime) -> None:
    """等待当前输入对应的行内联想就绪。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 1.0
    while loop.time() < deadline:
        if runtime.screen.input.buffer.suggestion is not None:
            return
        await asyncio.sleep(0.001)
    raise AssertionError("suggestion did not become ready")


async def wait_for_input_text(runtime: TuiRuntime, text: str) -> None:
    """等待管道输入被主输入框完整消费。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 1.0
    while loop.time() < deadline:
        if runtime.screen.input.buffer.text == text:
            return
        await asyncio.sleep(0.001)
    raise AssertionError(f"input text did not become {text!r}")


async def wait_for_submission(runtime: TuiRuntime):
    """等待输入处理结果进入提交队列。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 1.0
    while loop.time() < deadline:
        if not runtime.submissions.message_queue.empty():
            return runtime.submissions.message_queue.get_nowait()
        await asyncio.sleep(0.001)
    raise AssertionError("submission did not become ready")


@pytest.mark.anyio
async def test_bracketed_paste_sanitizes_control_characters() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        await runtime.open()
        try:
            pipe_input.send_text(
                "\x1b[200~BugID\t提交时间\r\n. 3117\t已解决\x00\x1b[201~"
            )
            await wait_for_input_text(
                runtime,
                "BugID   提交时间\n. 3117  已解决",
            )
        finally:
            await runtime.close()


def rendered_input_line(runtime: TuiRuntime) -> str:
    """返回最近一次渲染中的首行输入文本。"""
    screen = runtime.screen.application.renderer.last_rendered_screen
    position = screen.visible_windows_to_write_positions[
        runtime.screen.input.window
    ]
    row = screen.data_buffer[position.ypos]
    return "".join(
        row[column].char
        for column in range(position.xpos, position.xpos + position.width)
    ).rstrip()


def rendered_window_line(runtime: TuiRuntime, window) -> str:
    """返回指定窗口最近一次渲染的首行文本。"""
    screen = runtime.screen.application.renderer.last_rendered_screen
    position = screen.visible_windows_to_write_positions[window]
    row = screen.data_buffer[position.ypos]
    return "".join(
        row[column].char
        for column in range(position.xpos + position.width)
    ).rstrip()


def skill_spec(name: str) -> SkillSpec:
    """创建输入补全测试使用的 skill 描述。"""
    entry = Path(f"{name}/SKILL.md")
    return SkillSpec(
        name=name,
        description=f"Use {name}",
        source="test",
        root=entry.parent,
        entry=entry,
    )


def test_command_completion_discards_text_after_cursor() -> None:
    runtime = TuiRuntime()
    buffer  = runtime.screen.input.buffer
    buffer.document = Document("/mc xxxx", cursor_position=3)

    runtime.input_model.apply_completion(
        buffer,
        Completion("/mcp", start_position=-3),
    )

    assert buffer.text == "/mcp"
    assert buffer.cursor_position == len("/mcp")


def test_completion_surface_has_no_async_footer_gap() -> None:
    runtime = TuiRuntime()
    buffer  = runtime.screen.input.buffer
    buffer.document = Document("/mc", cursor_position=3)

    assert buffer.complete_state is None
    assert runtime.screen._completion_visible()
    assert runtime.screen._completion_height() == 1
    assert runtime.screen._completion_section_height() == 1
    assert (
        runtime.screen._input_stack_height()
        == runtime.screen._input_surface_height() + 1
    )
    assert not runtime.screen._footer_visible()

    buffer.document = Document("/mcp", cursor_position=4)

    assert runtime.screen._completion_visible()
    assert runtime.screen._completion_height() == 1
    assert not runtime.screen._footer_visible()
    assert "".join(
        text
        for _style, text in runtime.screen._completion_fallback_fragments()
    ).startswith("/mcp")


@pytest.mark.anyio
async def test_slash_completion_has_no_inline_ghost_text() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr(
                runtime.screen.application.output,
                "get_size",
                lambda: Size(rows=24, columns=80),
            )
            await runtime.open()
            try:
                with patch.object(
                    runtime.screen.input.buffer,
                    "start_completion",
                    wraps=runtime.screen.input.buffer.start_completion,
                ) as start_completion:
                    pipe_input.send_text("/")
                    await wait_for_completion(runtime)

                start_completion.assert_not_called()
                runtime.screen.application.invalidate()
                await asyncio.sleep(0)

                buffer = runtime.screen.input.buffer
                assert buffer.text == "/"
                assert buffer.suggestion is None
                assert buffer.complete_state is not None
                assert buffer.complete_state.complete_index == 0
                assert rendered_input_line(runtime) == "› /"
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_input_prompt_uses_single_space_before_placeholder() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        runtime.submissions.placeholder_text = "Write tests for @filename"

        await runtime.open()
        try:
            runtime.screen.application.invalidate()
            await asyncio.sleep(0)

            assert rendered_input_line(runtime) == "›  Write tests for @filename"
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_inline_command_hint_keeps_leading_space() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        await runtime.open()
        try:
            pipe_input.send_text("/model ")
            await wait_for_input_text(runtime, "/model ")
            await wait_for_suggestion(runtime)
            runtime.screen.application.invalidate()
            await asyncio.sleep(0)

            assert runtime.screen.input.buffer.suggestion is not None
            assert runtime.screen.input.buffer.suggestion.text == "<model-id>"
            assert rendered_input_line(runtime) == "› /model <model-id>"
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_slash_completion_aligns_with_input_command() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        await runtime.open()
        try:
            pipe_input.send_text("/s")
            await wait_for_completion(runtime)
            runtime.screen.application.invalidate()
            await asyncio.sleep(0)

            input_line = rendered_input_line(runtime)
            menu_window = runtime.screen.completion_menu.content
            completion_line = rendered_window_line(runtime, menu_window)
            screen = runtime.screen.application.renderer.last_rendered_screen
            bottom_position = screen.visible_windows_to_write_positions[
                runtime.screen.input_bottom_padding
            ]
            menu_position = screen.visible_windows_to_write_positions[menu_window]

            assert input_line == "› /s"
            assert completion_line.lstrip().startswith("/skills")
            assert input_line.index("/") == completion_line.index("/")
            assert menu_position.ypos == bottom_position.ypos + 1
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_exact_slash_completion_aligns_with_input_command() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        await runtime.open()
        try:
            pipe_input.send_text("/skills")
            await wait_for_input_text(runtime, "/skills")
            runtime.screen.application.invalidate()
            await asyncio.sleep(0)

            input_line = rendered_input_line(runtime)
            completion_line = rendered_window_line(
                runtime,
                runtime.screen.completion_fallback_window,
            )

            assert input_line == "› /skills"
            assert completion_line.lstrip().startswith("/skills")
            assert input_line.index("/") == completion_line.index("/")
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_slash_prefix_selects_first_match_without_rewriting_input() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        await runtime.open()
        try:
            pipe_input.send_text("/f")
            await wait_for_completion(runtime)

            buffer = runtime.screen.input.buffer
            assert buffer.text == "/f"
            assert buffer.complete_state is not None
            assert buffer.complete_state.complete_index == 0
            assert buffer.complete_state.current_completion.display_text == "/fast"
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_tab_completes_selected_slash_command_without_submitting() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        await runtime.open()
        try:
            pipe_input.send_text("/f")
            await wait_for_completion(runtime)
            pipe_input.send_text("\t")
            await wait_for_input_text(runtime, "/fast")

            assert runtime.submissions.message_queue.empty()
            assert runtime.screen._completion_fallback_visible()
            assert "".join(
                text
                for _style, text in runtime.screen._completion_fallback_fragments()
            ).startswith("/fast")
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_enter_executes_the_default_root_command() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        await runtime.open()
        try:
            pipe_input.send_text("/\r")
            submission = await wait_for_submission(runtime)

            assert submission.value == "/chat"
            assert runtime.screen.input.buffer.text == ""
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_enter_opens_parameter_input_for_complete_command() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        await runtime.open()
        try:
            pipe_input.send_text("/model\r")
            await wait_for_input_text(runtime, "/model ")

            assert runtime.submissions.message_queue.empty()
            assert not runtime.screen._completion_visible()
            assert runtime.screen.input.buffer.suggestion is not None
            assert runtime.screen.input.buffer.suggestion.text == "<model-id>"
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_unknown_slash_command_renders_non_selectable_empty_state() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        await runtime.open()
        try:
            pipe_input.send_text("/aaa")
            await wait_for_input_text(runtime, "/aaa")
            runtime.screen.application.invalidate()
            await asyncio.sleep(0)

            buffer = runtime.screen.input.buffer
            assert buffer.complete_state is None
            assert runtime.screen._completion_fallback_visible()
            assert runtime.screen._completion_fallback_fragments() == [
                ("class:completion-menu.empty", "no matches"),
            ]
            assert rendered_window_line(
                runtime,
                runtime.screen.completion_fallback_window,
            ).lstrip() == "no matches"
        finally:
            await runtime.close()


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
            assert runtime.screen._completion_height() == 7
            assert runtime.screen.completion_menu.content.right_margins

            runtime.input_model._select_completion(buffer, 11)

            assert buffer.complete_state.current_completion.text == "$skill-12 "
        finally:
            await runtime.close()


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
                ("class:completion-menu.empty", "no matches"),
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
async def test_backspace_refreshes_inline_suggestion() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        await runtime.open()
        try:
            pipe_input.send_text("hiX")
            await wait_for_input_text(runtime, "hiX")

            buffer = runtime.screen.input.buffer
            assert buffer.suggestion is None

            pipe_input.send_text("\x7f")
            await wait_for_input_text(runtime, "hi")
            await wait_for_suggestion(runtime)

            assert buffer.text == "hi"
            assert buffer.suggestion is not None
            assert buffer.suggestion.text == "，请介绍一下你自己"
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
            ] == ["/skills", "/shutdown"]
        finally:
            await runtime.close()
