# -*- coding: utf-8 -*-

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from mind_app.tui.core.runtime import TuiRuntime
from mind_app.tui.session.loop import preload_tui_prompt_context


@pytest.mark.anyio
async def test_prompt_context_is_loaded_before_runtime_open() -> None:
    runtime = TuiRuntime()
    workspace_updates = []
    mind = SimpleNamespace(
        frontend=SimpleNamespace(runtime=runtime),
        fresh_pref_config=AsyncMock(return_value={
            "primary": {
                "model": "gpt-test",
                "reasoning_effort": "high",
            },
        }),
        native_coding=SimpleNamespace(
            running_exec_sessions=AsyncMock(return_value={
                "count": 1,
                "items": [{"command": "pytest -q"}],
            }),
        ),
        set_history_workspace=workspace_updates.append,
    )

    with patch(
        "mind_app.tui.session.loop.fetch_runtime_workspace_root",
        AsyncMock(return_value=Path("D:/workspace")),
    ):
        await preload_tui_prompt_context(mind)

    assert not runtime.active
    assert runtime.context.model == "gpt-test high"
    assert runtime.context.access_label
    assert runtime.process_status.label == "pytest -q"
    assert workspace_updates == [Path("D:/workspace")]
    mind.fresh_pref_config.assert_awaited_once_with(ttl_sec=0.0)

    preloaded_placeholder = runtime.placeholder_text
    runtime.message_queue.put_nowait("hello")

    value = await runtime.read_message(runtime.context)

    assert value == "hello"
    assert runtime.placeholder_text == preloaded_placeholder


def test_successful_submission_prepares_next_placeholder() -> None:
    runtime = TuiRuntime()
    runtime.input.buffer.text = "hello"

    with patch.object(
        runtime.input_model,
        "new_placeholder",
        return_value="next placeholder",
    ) as new_placeholder:
        runtime._accept_input(runtime.input.buffer)

    assert runtime.placeholder_text == "next placeholder"
    new_placeholder.assert_called_once_with("chat")
