# -*- coding: utf-8 -*-

import asyncio
from io import StringIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from metadata import const

from mind_app.presentation.terminal.turn_lifecycle import (
    ControllerTurnForegroundLifecycle,
    run_foreground_turn,
)
from mind_app.approval.coordinator import ApprovalCoordinator
from mind_app.interaction.contracts import PromptContext
from frontends.tui.core.runtime import TuiRuntime
from mind_app.presentation.terminal.progress import (
    OscTerminalProgress,
    PassiveTerminalProgress,
    TERMINAL_TITLE_ACTION_PREFIXES,
    TERMINAL_TITLE_SPINNER_FRAMES,
    create_terminal_progress,
)


class TerminalStream(StringIO):
    def __init__(self, *, interactive: bool = True) -> None:
        super().__init__()
        self.interactive = interactive
        self.flush_count = 0

    def isatty(self) -> bool:
        return self.interactive

    def flush(self) -> None:
        self.flush_count += 1
        super().flush()


@pytest.mark.parametrize(
    ("environ", "interactive", "expected_type"),
    (
        ({"WT_SESSION": "1"}, True, OscTerminalProgress),
        ({"TERM_PROGRAM": "iTerm.app"}, True, OscTerminalProgress),
        ({"TERM_PROGRAM": "Apple_Terminal"}, True, OscTerminalProgress),
        ({"TERM": "xterm-256color"}, True, OscTerminalProgress),
        ({"TERM": "dumb"}, True, PassiveTerminalProgress),
        ({"WT_SESSION": "1"}, False, PassiveTerminalProgress),
    ),
)
def test_terminal_progress_requires_supported_interactive_terminal(
    environ,
    interactive: bool,
    expected_type,
) -> None:
    progress = create_terminal_progress(
        TerminalStream(interactive=interactive),
        environ,
    )

    assert isinstance(progress, expected_type)


def test_osc_terminal_progress_writes_state_changes() -> None:
    stream = TerminalStream()
    progress = OscTerminalProgress(stream)

    progress.begin()
    progress.begin()
    progress.warning()
    progress.begin()
    progress.clear()
    progress.clear()

    assert stream.getvalue() == (
        f"\x1b]0;{TERMINAL_TITLE_SPINNER_FRAMES[0]} {const.APP_DESC}\x07"
        f"\x1b]0;{TERMINAL_TITLE_ACTION_PREFIXES[0]}\x07"
        f"\x1b]0;{TERMINAL_TITLE_SPINNER_FRAMES[0]} {const.APP_DESC}\x07"
        "\x1b]0;\x07"
    )
    assert stream.flush_count == 4


def test_osc_terminal_progress_restores_workspace_title_when_idle() -> None:
    stream = TerminalStream()
    progress = OscTerminalProgress(stream)

    progress.set_workspace_title("ProxyMind")
    progress.begin()
    progress.clear()
    progress.close()

    assert stream.getvalue() == (
        "\x1b]0;ProxyMind\x07"
        f"\x1b]0;{TERMINAL_TITLE_SPINNER_FRAMES[0]} ProxyMind\x07"
        "\x1b]0;ProxyMind\x07"
        "\x1b]0;\x07"
    )


def test_osc_terminal_progress_sanitizes_workspace_title() -> None:
    stream = TerminalStream()
    progress = OscTerminalProgress(stream)

    progress.set_workspace_title("  Project\x1b]0;Injected\x07\n  ")

    assert stream.getvalue() == "\x1b]0;Project]0;Injected\x07"


@pytest.mark.parametrize(
    ("workspace_label", "expected_title"),
    (
        (r"D:\Projects\ProxyMind", "ProxyMind"),
        ("~/Projects/Craft", "Craft"),
        ("?", ""),
    ),
)
def test_tui_prompt_context_updates_idle_workspace_title(
    workspace_label: str,
    expected_title: str,
) -> None:
    progress = SimpleNamespace(
        set_workspace_title=Mock(),
        begin=Mock(),
        warning=Mock(),
        clear=Mock(),
        close=Mock(),
    )
    runtime = TuiRuntime(terminal_progress=progress)

    runtime.set_prompt_context(PromptContext(
        model="model",
        workspace_label=workspace_label,
    ))

    progress.set_workspace_title.assert_called_once_with(expected_title)


@pytest.mark.anyio
async def test_osc_terminal_progress_animates_title() -> None:
    stream = TerminalStream()
    progress = OscTerminalProgress(stream)

    progress.begin()
    await asyncio.sleep(0.12)
    progress.clear()

    assert stream.getvalue().startswith(
        f"\x1b]0;{TERMINAL_TITLE_SPINNER_FRAMES[0]} {const.APP_DESC}\x07"
        f"\x1b]0;{TERMINAL_TITLE_SPINNER_FRAMES[1]} {const.APP_DESC}\x07"
    )
    assert stream.getvalue().endswith("\x1b]0;\x07")


@pytest.mark.anyio
async def test_osc_terminal_progress_blinks_action_title(monkeypatch) -> None:
    monkeypatch.setattr(
        "mind_app.presentation.terminal.progress.TERMINAL_TITLE_ACTION_INTERVAL",
        0.01,
    )
    stream = TerminalStream()
    progress = OscTerminalProgress(stream)

    progress.warning()
    await asyncio.sleep(0.02)
    progress.clear()

    assert stream.getvalue().startswith(
        f"\x1b]0;{TERMINAL_TITLE_ACTION_PREFIXES[0]}\x07"
        f"\x1b]0;{TERMINAL_TITLE_ACTION_PREFIXES[1]}\x07"
    )
    assert stream.getvalue().endswith("\x1b]0;\x07")


@pytest.mark.anyio
async def test_tui_approval_switches_terminal_progress_to_warning() -> None:
    progress = SimpleNamespace(
        begin=Mock(),
        warning=Mock(),
        clear=Mock(),
    )
    runtime = TuiRuntime(terminal_progress=progress)
    runtime.begin_terminal_progress()
    progress.begin.reset_mock()
    runtime.screen.approval.request = AsyncMock(return_value="accept")

    decision = await ApprovalCoordinator(runtime).request({})

    assert decision == "accept"
    progress.warning.assert_called_once_with()
    progress.begin.assert_called_once_with()


@pytest.mark.anyio
async def test_tui_approval_batch_switches_terminal_progress_once() -> None:
    progress = SimpleNamespace(
        begin=Mock(),
        warning=Mock(),
        clear=Mock(),
    )
    runtime = TuiRuntime(terminal_progress=progress)
    coordinator = ApprovalCoordinator(runtime)
    runtime.begin_terminal_progress()
    progress.begin.reset_mock()
    first = asyncio.create_task(coordinator.request({
        "id": "first",
        "tool": "shell_command",
        "command": "echo first",
        "show_timer": False,
    }))
    second = asyncio.create_task(coordinator.request({
        "id": "second",
        "tool": "shell_command",
        "command": "echo second",
        "show_timer": False,
    }))
    for _ in range(40):
        state = runtime.screen.approval.state
        if state is not None and state.presentation.context.approval_id == "first":
            break
        await asyncio.sleep(0)
    else:
        raise AssertionError("first approval was not presented")

    runtime.screen.approval.finish("accept")
    assert await first == "accept"
    for _ in range(40):
        state = runtime.screen.approval.state
        if state is not None and state.presentation.context.approval_id == "second":
            break
        await asyncio.sleep(0)
    else:
        raise AssertionError("second approval was not presented")
    runtime.screen.approval.finish("decline")

    assert await second == "decline"
    progress.warning.assert_called_once_with()
    progress.begin.assert_called_once_with()


@pytest.mark.anyio
async def test_tui_approval_does_not_restart_finished_terminal_progress() -> None:
    calls = []
    decision_ready = asyncio.Event()

    class Progress(object):
        def begin(self) -> None:
            calls.append("begin")

        def warning(self) -> None:
            calls.append("warning")

        def clear(self) -> None:
            calls.append("clear")

    runtime = TuiRuntime(terminal_progress=Progress())

    async def wait_for_decision(approval) -> str:
        await decision_ready.wait()
        return "accept"

    runtime.screen.approval.request = wait_for_decision

    runtime.begin_terminal_progress()
    approval_task = asyncio.create_task(
        ApprovalCoordinator(runtime).request({})
    )
    for _ in range(40):
        if "warning" in calls:
            break
        await asyncio.sleep(0)
    else:
        raise AssertionError("approval session did not start")

    runtime.end_terminal_progress()
    decision_ready.set()

    assert await approval_task == "accept"
    assert calls == ["begin", "warning", "clear", "clear"]


@pytest.mark.anyio
async def test_turn_lifecycle_clears_terminal_progress_on_failure() -> None:
    events = []
    frontend_runtime = SimpleNamespace(
        begin_terminal_progress=lambda: events.append("progress.begin"),
        end_terminal_progress=lambda: events.append("progress.clear"),
    )
    mind = SimpleNamespace(
        animate=False,
        frontend=SimpleNamespace(runtime=frontend_runtime),
        start_anim=AsyncMock(side_effect=lambda: events.append("anim.begin")),
        stop_anim=Mock(side_effect=lambda _kind: _stop_anim(events)),
        await_cleanup=AsyncMock(side_effect=_await_cleanup),
    )

    async def runner(**_kwargs) -> None:
        events.append("runner")
        raise RuntimeError("failed")

    with pytest.raises(RuntimeError, match="failed"):
        await run_foreground_turn(ControllerTurnForegroundLifecycle(mind), runner)

    assert events == [
        "progress.begin",
        "anim.begin",
        "runner",
        "anim.clear",
        "progress.clear",
    ]


async def _stop_anim(events: list[str]) -> None:
    events.append("anim.clear")


async def _await_cleanup(awaitable) -> None:
    await awaitable
