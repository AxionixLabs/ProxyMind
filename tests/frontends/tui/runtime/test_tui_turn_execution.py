# -*- coding: utf-8 -*-

import asyncio

import pytest

from agent.application.turns.run_result import RunResult
from agent.domain.policies import preset_permissions
from frontends.tui.session.turn import run_tui_model_turn


class _RecordingRootTurnRunner:
    """记录 TUI 向组合根提交的稳定轮次参数。"""

    def __init__(self, result: RunResult | BaseException) -> None:
        self.result = result
        self.calls: list[tuple[dict, str, dict]] = []

    async def __call__(self, pref_config=None, *, message: str, **kwargs):
        self.calls.append((pref_config, message, kwargs))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


@pytest.mark.anyio
async def test_tui_turn_submits_frozen_input_to_bound_root_runner() -> None:
    expected = RunResult(status="completed", assistant_text="done")
    runner = _RecordingRootTurnRunner(expected)
    permissions = preset_permissions("auto")
    prepared_attachments: list[dict] = []

    result = await run_tui_model_turn(
        runner,
        message_text="",
        pref_config={"primary": {"model": "test-model"}},
        permissions=permissions,
        attachments=({
            "filename": "screen.png",
            "kind": "image",
        },),
        environment_snapshot={"snapshot_id": "envsnap_tui"},
        turn_id="turn_tui",
        prompt_extras={"selection": {"x": 10, "y": 20}},
        on_prompt_prepared=prepared_attachments.extend,
    )

    assert result == expected
    assert prepared_attachments == [{
        "filename": "screen.png",
        "kind": "image",
    }]
    assert runner.calls == [(
        {"primary": {"model": "test-model"}},
        "",
        {
            "title": "screen.png",
            "source": "tui",
            "permissions": permissions,
            "attachments": [{
                "filename": "screen.png",
                "kind": "image",
            }],
            "exec_env": {"snapshot_id": "envsnap_tui"},
            "turn_id": "turn_tui",
            "extras": {"selection": {"x": 10, "y": 20}},
        },
    )]


@pytest.mark.anyio
async def test_tui_turn_uses_message_as_session_title() -> None:
    runner = _RecordingRootTurnRunner(RunResult(status="completed"))

    await run_tui_model_turn(
        runner,
        message_text="inspect the workspace",
        pref_config={},
        permissions=preset_permissions("read-only"),
    )

    assert runner.calls[0][2]["title"] == "inspect the workspace"
    assert runner.calls[0][2]["source"] == "tui"
    assert runner.calls[0][2]["attachments"] == []
    assert runner.calls[0][2]["exec_env"] is None


@pytest.mark.anyio
async def test_tui_turn_propagates_root_runner_failure() -> None:
    runner = _RecordingRootTurnRunner(RuntimeError("stream failed"))

    with pytest.raises(RuntimeError, match="stream failed"):
        await run_tui_model_turn(
            runner,
            message_text="hello",
            pref_config={},
            permissions=preset_permissions("auto"),
        )


@pytest.mark.anyio
async def test_tui_turn_propagates_root_runner_cancellation() -> None:
    runner = _RecordingRootTurnRunner(asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await run_tui_model_turn(
            runner,
            message_text="hello",
            pref_config={},
            permissions=preset_permissions("auto"),
        )
