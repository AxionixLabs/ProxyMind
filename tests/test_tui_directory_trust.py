# -*- coding: utf-8 -*-

import asyncio
from pathlib import Path

import pytest
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.utils import get_cwidth

from mind_app.tui.core.directory_trust import TuiDirectoryTrust
from mind_app.tui.core.runtime import TuiRuntime


def _text(prompt: TuiDirectoryTrust) -> str:
    return "".join(text for _style, text in prompt.fragments())


@pytest.mark.anyio
async def test_directory_trust_prompt_matches_startup_layout() -> None:
    prompt = TuiDirectoryTrust(
        invalidate=lambda: None,
        focus_prompt=lambda: None,
        focus_input=lambda: None,
        get_width=lambda: 70,
        get_max_height=lambda: 24,
    )
    prompt.begin(Path("/workspace/project"), Path("/workspace/project"))

    assert _text(prompt) == (
        "> You are in /workspace/project\n"
        "\n"
        "  Do you trust the contents of this directory? Working with untrusted\n"
        "  contents comes with higher risk of prompt injection. Trusting the\n"
        "  directory allows project-local config, MCP servers, and hooks to\n"
        "  load.\n"
        "\n"
        "› 1. Yes, continue\n"
        "  2. No, quit\n"
        "\n"
        "  Press enter to continue"
    )
    assert prompt.height() == 11
    assert any(
        style == "class:directory-trust.option.selected"
        and text == "› 1. Yes, continue"
        for style, text in prompt.fragments()
    )

    prompt.close()


@pytest.mark.anyio
async def test_directory_trust_prompt_wraps_and_reports_root_at_narrow_width() -> None:
    width = 32
    prompt = TuiDirectoryTrust(
        invalidate=lambda: None,
        focus_prompt=lambda: None,
        focus_input=lambda: None,
        get_width=lambda: width,
        get_max_height=lambda: 40,
    )
    prompt.begin(Path("/repo/src"), Path("/repo"))

    text = _text(prompt)

    assert "You're in a subdirectory" in text
    assert "apply to the project root: /repo" in text.replace("\n  ", " ")
    assert all(get_cwidth(line) <= width for line in text.splitlines())
    assert prompt.height() == len(text.splitlines())

    prompt.close()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("keys", "trusted"),
    (("1", True), ("n", False), ("j\r", False)),
)
async def test_directory_trust_uses_single_application_and_local_keys(
    keys: str,
    trusted: bool,
) -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(
            input_obj=pipe_input,
            output_obj=DummyOutput(),
        )
        application = runtime.screen.application

        await runtime.begin_directory_trust(
            Path("/workspace/project"),
            Path("/workspace/project"),
        )
        try:
            assert runtime.screen.application is application
            assert runtime.screen.directory_trust.active
            assert (
                application.layout.current_control
                is runtime.screen.directory_trust_control
            )
            assert runtime.document.blocks == []

            pipe_input.send_text("\x14")
            await asyncio.sleep(0)
            assert not runtime.screen.transcript_overlay.active
            assert runtime.screen.directory_trust.active

            pipe_input.send_text(keys)

            assert await asyncio.wait_for(
                runtime.wait_directory_trust(),
                timeout=1.0,
            ) is trusted
            assert runtime.document.blocks == []
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_directory_trust_defers_hidden_startup_animation() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(
            input_obj=pipe_input,
            output_obj=DummyOutput(),
        )
        played: list[str] = []

        async def startup_animation() -> None:
            played.append("intro")

        runtime.set_startup_animation(startup_animation)

        await runtime.begin_directory_trust(
            Path("/workspace/project"),
            Path("/workspace/project"),
        )
        try:
            assert played == []

            await runtime.finish_directory_trust()

            assert played == ["intro"]
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_directory_trust_error_reuses_the_same_surface() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(
            input_obj=pipe_input,
            output_obj=DummyOutput(),
        )

        await runtime.begin_directory_trust(
            Path("/workspace/project"),
            Path("/workspace/project"),
        )
        try:
            pipe_input.send_text("1")
            assert await asyncio.wait_for(
                runtime.wait_directory_trust(),
                timeout=1.0,
            )

            runtime.show_directory_trust_error("config is invalid")

            assert runtime.screen.directory_trust.active
            assert "  config is invalid" in _text(
                runtime.screen.directory_trust
            )
            assert runtime.document.blocks == []

            pipe_input.send_text("n")
            assert not await asyncio.wait_for(
                runtime.wait_directory_trust(),
                timeout=1.0,
            )
        finally:
            await runtime.close()


if __name__ == '__main__':
    pass
