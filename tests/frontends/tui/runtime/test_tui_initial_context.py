# -*- coding: utf-8 -*-

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from frontends.tui.core.runtime import TuiRuntime
from frontends.tui.session.state import (
    TuiSessionState,
    preload_tui_prompt_context,
)
from agent.domain.policies import preset_permissions


@pytest.mark.anyio
async def test_prompt_context_is_loaded_before_runtime_open() -> None:
    runtime = TuiRuntime()
    workspace_updates = []
    host = SimpleNamespace(
        history_workspace=Path("D:/workspace"),
        frontend=SimpleNamespace(runtime=runtime),
        settings=SimpleNamespace(
            config=SimpleNamespace(load=lambda: {
                "skills": {"enabled": [], "disabled": []},
            }),
            fresh_preferences=AsyncMock(return_value={
                "primary": {
                    "model": "gpt-test",
                    "reasoning_effort": "high",
                },
            }),
            permissions=preset_permissions("auto"),
        ),
        workspace_runtime=SimpleNamespace(
            coding=SimpleNamespace(
                running_exec_sessions=AsyncMock(return_value={
                    "count": 1,
                    "items": [{"command": "pytest -q"}],
                }),
            ),
        ),
        set_history_workspace=workspace_updates.append,
    )

    await preload_tui_prompt_context(host)

    assert not runtime.active
    assert runtime.context.model == "gpt-test high"
    assert runtime.context.permissions_label == "Ask for approval"
    assert runtime.screen.process_status.label == (
        "1 background terminal running · /ps to view · /stop to close"
    )
    assert runtime.screen.background_shell_status.label == (
        "1 background terminal running · /ps to view · /stop to close"
    )
    assert workspace_updates == []
    host.settings.fresh_preferences.assert_awaited_once_with(ttl_sec=0.0)

    preloaded_placeholder = runtime.submissions.placeholder_text
    runtime.submissions.message_queue.put_nowait("hello")

    value = await runtime.read_message(runtime.context)

    assert value == "hello"
    assert runtime.submissions.placeholder_text == preloaded_placeholder


@pytest.mark.anyio
async def test_first_trust_keeps_input_hidden_until_startup_finishes() -> None:
    with create_pipe_input() as pipe_input:
        workspace = Path("/workspace/project")
        runtime = TuiRuntime(
            input_obj=pipe_input,
            output_obj=DummyOutput(),
        )
        played = []

        async def startup_animation() -> None:
            played.append("intro")

        runtime.set_startup_animation(startup_animation)
        runtime.begin_startup_gate()
        await runtime.begin_directory_trust(
            workspace,
            workspace,
        )
        try:
            pipe_input.send_text("1")
            assert await runtime.wait_directory_trust()
            assert runtime.directory_trust_active
            assert played == []

            host = SimpleNamespace(
                history_workspace=workspace,
                frontend=SimpleNamespace(runtime=runtime),
                settings=SimpleNamespace(
                    config=SimpleNamespace(load=lambda: {
                        "skills": {"enabled": [], "disabled": []},
                    }),
                    fresh_preferences=AsyncMock(return_value={
                        "primary": {
                            "model": "gpt-test",
                            "reasoning_effort": "high",
                        },
                    }),
                    permissions=preset_permissions("auto"),
                ),
                workspace_runtime=SimpleNamespace(
                    coding=SimpleNamespace(
                        running_exec_sessions=AsyncMock(return_value={}),
                    ),
                ),
                set_history_workspace=lambda _workspace: None,
            )

            await preload_tui_prompt_context(host)

            footer = "".join(
                text for _style, text in runtime.screen._footer_fragments()
            )
            assert not runtime.directory_trust_active
            assert runtime.startup_gate_active
            assert (
                runtime.screen.application.layout.current_control
                is runtime.screen.startup_menu_control
            )
            assert runtime.context.model == "gpt-test high"
            assert "gpt-test high" in footer
            assert "Ask for approval" in footer
            assert str(workspace.resolve()) in footer
            assert " · -" not in footer
            assert played == []

            await runtime.finish_startup_gate()

            assert not runtime.startup_gate_active
            assert (
                runtime.screen.application.layout.current_control
                is runtime.screen.input.control
            )
            assert played == ["intro"]
        finally:
            await runtime.close()


def test_successful_submission_prepares_next_placeholder() -> None:
    runtime = TuiRuntime()
    runtime.screen.input.buffer.text = "hello"

    with patch.object(
        runtime.input_model,
        "new_placeholder",
        return_value="next placeholder",
    ) as new_placeholder:
        runtime.submissions.accept_input(runtime.screen.input.buffer)

    assert runtime.submissions.placeholder_text == "next placeholder"
    new_placeholder.assert_called_once_with()


@pytest.mark.anyio
async def test_prompt_refresh_keeps_selected_workspace(tmp_path: Path) -> None:
    target = tmp_path / "resumed"
    target.mkdir()
    runtime = TuiRuntime()
    host = SimpleNamespace(
        history_workspace=str(target), frontend=SimpleNamespace(runtime=runtime),
        settings=SimpleNamespace(fresh_preferences=AsyncMock(return_value={})),
    )
    state = TuiSessionState(
        pref_config={}, model="", workspace_label="old", permissions=preset_permissions("auto"),
    )
    state.workspace_refreshed_at = 0
    await state.refresh_for_prompt(host)
    assert state.workspace_label.endswith("resumed")
    assert host.history_workspace == str(target)
