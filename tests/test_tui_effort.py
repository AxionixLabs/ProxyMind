# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from frontends.tui.core.runtime import TuiRuntime
from frontends.tui.core.models import (
    MenuDescriptionLayout,
    STANDARD_MENU_FOOTER_HINT,
)
from frontends.tui.features.model import choose_model_effort
from frontends.tui.session import dispatch
from frontends.tui.session import loop
from agent.domain.policies import preset_permissions
from agent.application.turns.commands import TurnApplication
from agent.harness.sessions.owner import SessionRuntimeOwner
from agent.harness.process_lifecycle import ProcessLifecycle


@pytest.fixture(autouse=True)
def frozen_environment_snapshot(monkeypatch) -> None:
    """固定 TUI 命令提交时捕获的环境事实。"""
    monkeypatch.setattr(
        loop,
        "capture_active_turn_environment",
        Mock(return_value={"snapshot_id": "envsnap_tui"}),
    )
    monkeypatch.setattr(
        loop,
        "TurnApplication",
        lambda: TurnApplication(runtime_factory=SessionRuntimeOwner),
    )


@pytest.mark.anyio
async def test_tui_uses_durable_runtime_composition_for_real_layout(
    monkeypatch,
    tmp_path,
) -> None:
    close = AsyncMock()
    application = SimpleNamespace(close=close)
    open_application = Mock(return_value=application)
    run_loop = AsyncMock()
    db_path = tmp_path / "runtime.db"
    monkeypatch.setattr(loop, "agent_runtime_db_path", lambda: db_path)
    monkeypatch.setattr(loop, "_run_tui_loop", run_loop)

    await loop.run_tui_loop(
        SimpleNamespace(application_layout=object()),
        turn_application_factory=open_application,
        turn_runner=AsyncMock(),
    )

    open_application.assert_called_once_with(db_path)
    assert run_loop.await_args.kwargs["turn_application"] is application
    assert run_loop.await_args.kwargs["local_session_id"] is None
    close.assert_awaited_once_with(cancel_running=True)


@pytest.mark.anyio
async def test_tui_requires_explicit_turn_application_factory_for_real_layout() -> None:
    with pytest.raises(
        RuntimeError,
        match="TUI turn application factory is required",
    ):
        await loop.run_tui_loop(SimpleNamespace(application_layout=object()))


@pytest.mark.anyio
async def test_tui_requires_explicit_root_turn_runner() -> None:
    with pytest.raises(
        RuntimeError,
        match="TUI root turn runner is required",
    ):
        await loop.run_tui_loop(SimpleNamespace())


@pytest.mark.anyio
async def test_effort_menu_uses_primary_selection_contract() -> None:
    runtime = TuiRuntime()
    runtime.select_menu = AsyncMock(return_value="high")

    selected = await choose_model_effort(runtime, "high")

    request = runtime.select_menu.await_args.args[0]
    assert selected == "high"
    assert request.view_id == "model:effort"
    assert request.title == "Update Reasoning Effort"
    assert request.title_accent_suffix == ""
    assert request.status == (
        "Choose the reasoning effort used by the primary model."
    )
    assert request.help_text == ""
    assert request.footer_hint == STANDARD_MENU_FOOTER_HINT
    assert (
        request.description_layout
        is MenuDescriptionLayout.STACK_BELOW_WHEN_NARROW
    )
    assert [option.is_current for option in request.options] == [
        False,
        False,
        True,
        False,
    ]
    assert request.selected == 2


@pytest.mark.anyio
async def test_effort_command_updates_footer_context_immediately(
    monkeypatch,
) -> None:
    runtime = TuiRuntime()
    lifecycle = ProcessLifecycle()
    stale_config = {
        "primary": {
            "model": "test-model",
            "reasoning_effort": "medium",
        },
    }
    mind = SimpleNamespace(
        subscription=SimpleNamespace(current=None),
        lifecycle=lifecycle,
        settings=SimpleNamespace(
            preference_config=lambda: stale_config,
            permissions=preset_permissions("auto"),
            fresh_preferences=AsyncMock(return_value=stale_config),
        ),
        frontend=SimpleNamespace(
            runtime=runtime,
            interaction=runtime,
            application=SimpleNamespace(emit=Mock()),
        ),
    )

    async def monitor_exec_status(_runtime, _mind) -> None:
        return None

    def render_status(_application, _effort) -> None:
        lifecycle.request_stop()

    monkeypatch.setattr(loop, "monitor_exec_status", monitor_exec_status)
    monkeypatch.setattr(
        dispatch,
        "choose_model_effort",
        AsyncMock(return_value="high"),
    )
    monkeypatch.setattr(
        dispatch,
        "persist_primary_pref",
        AsyncMock(return_value={
            "model": "test-model",
            "reasoning_effort": "high",
        }),
    )
    monkeypatch.setattr(dispatch, "render_model_effort_status", render_status)

    runtime.submissions.message_queue.put_nowait("/effort")
    await loop.run_tui_loop(
        mind,
        turn_runner=AsyncMock(),
    )

    assert runtime.context.model == "test-model high"
