# -*- coding: utf-8 -*-

from io import StringIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from mind_app.runtime.support.calling import run_mode_lifecycle
from mind_app.tui.core.runtime import TuiRuntime
from mind_core.design.terminal_progress import (
    OSC_PROGRESS_CLEAR,
    OSC_PROGRESS_INDETERMINATE,
    OSC_PROGRESS_WARNING,
    OscTerminalProgress,
    PassiveTerminalProgress,
    create_terminal_progress,
    terminal_kind,
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
    ("environ", "kind"),
    (
        ({"WT_SESSION": "1"}, "windows_terminal"),
        ({"TERM_PROGRAM": "iTerm.app"}, "iterm2"),
        ({"TERM_PROGRAM": "Apple_Terminal"}, "apple_terminal"),
        ({"TERM_PROGRAM": "vscode"}, "unknown"),
    ),
)
def test_terminal_kind_uses_terminal_environment(environ, kind: str) -> None:
    assert terminal_kind(environ) == kind


@pytest.mark.parametrize(
    ("environ", "interactive", "expected_type"),
    (
        ({"WT_SESSION": "1"}, True, OscTerminalProgress),
        ({"TERM_PROGRAM": "iTerm.app"}, True, OscTerminalProgress),
        ({"TERM_PROGRAM": "Apple_Terminal"}, True, PassiveTerminalProgress),
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
        OSC_PROGRESS_INDETERMINATE
        + OSC_PROGRESS_WARNING
        + OSC_PROGRESS_INDETERMINATE
        + OSC_PROGRESS_CLEAR
    )
    assert stream.flush_count == 4


@pytest.mark.anyio
async def test_tui_approval_switches_terminal_progress_to_warning() -> None:
    progress = SimpleNamespace(
        begin=Mock(),
        warning=Mock(),
        clear=Mock(),
    )
    runtime = TuiRuntime(terminal_progress=progress)
    runtime.screen.approval.request = AsyncMock(return_value="accept")

    decision = await runtime.request_approval({})

    assert decision == "accept"
    progress.warning.assert_called_once_with()
    progress.begin.assert_called_once_with()


@pytest.mark.anyio
async def test_mode_lifecycle_clears_terminal_progress_on_failure() -> None:
    events = []
    frontend_runtime = SimpleNamespace(
        begin_terminal_progress=lambda: events.append("progress.begin"),
        end_terminal_progress=lambda: events.append("progress.clear"),
    )
    mind = SimpleNamespace(
        animate=False,
        frontend=SimpleNamespace(runtime=frontend_runtime),
        start_anim=AsyncMock(side_effect=lambda _mode: events.append("anim.begin")),
        stop_anim=Mock(side_effect=lambda _kind: _stop_anim(events)),
        await_cleanup=AsyncMock(side_effect=_await_cleanup),
    )

    async def runner(**_kwargs) -> None:
        events.append("runner")
        raise RuntimeError("failed")

    with pytest.raises(RuntimeError, match="failed"):
        await run_mode_lifecycle(mind, runner)

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
