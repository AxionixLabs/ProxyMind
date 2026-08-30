# -*- coding: utf-8 -*-

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from infrastructure.platform.git_diff import (
    WorkspaceDiffError,
    WorkspaceDiffResult,
    WorkspaceDiffState,
)
from mind_app.tui.contracts.pager import StaticPagerRequest
from mind_app.tui.core.render import fragments_text
from mind_app.tui.core.runtime import TuiRuntime
from mind_app.tui.core.static_pager import TuiStaticPager
from mind_app.tui.rendering.screen.layout import measure_overlay_layout
from mind_app.tui.features import diff as diff_feature
from mind_app.tui.session import dispatch as dispatch_module
from mind_app.tui.session.dispatch import (
    DispatchAction,
    TuiCommandDispatcher,
)


class _DiffService(object):
    result = WorkspaceDiffResult(WorkspaceDiffState.READY)
    error: WorkspaceDiffError | None = None
    on_compute = None

    async def compute(self, cwd: Path) -> WorkspaceDiffResult:
        callback = type(self).on_compute
        if callback is not None:
            callback(cwd)
        error = type(self).error
        if error is not None:
            raise error
        return type(self).result


def _request(*lines: str) -> StaticPagerRequest:
    return StaticPagerRequest(
        title="D I F F",
        lines=tuple((("", line),) for line in lines),
    )


def test_static_pager_wraps_scrolls_and_reports_progress() -> None:
    size = {"width": 5, "height": 2}
    invalidated = Mock()
    pager = TuiStaticPager(
        get_width=lambda: size["width"],
        get_height=lambda: size["height"],
        invalidate=invalidated,
    )
    pager.open(_request("123456", "second", "third"))

    assert fragments_text(pager.visible_fragments()) == "12345\n6"
    assert pager.scroll_percentage() == 0

    pager.scroll_page(1)

    assert fragments_text(pager.visible_fragments()) == "secon\nd"
    assert pager.scroll_percentage() == 67

    pager.jump(to_end=True)

    assert fragments_text(pager.visible_fragments()) == "d\nthird"
    assert pager.scroll_percentage() == 100


def test_static_pager_uses_codex_half_up_scroll_percentage() -> None:
    pager = TuiStaticPager(
        get_width=lambda: 10,
        get_height=lambda: 1,
        invalidate=lambda: None,
    )
    pager.open(_request(*(str(index) for index in range(41))))

    pager.scroll_lines(1)

    assert pager.scroll_percentage() == 3


def test_static_pager_reserves_codex_footer_and_bottom_spacing() -> None:
    layout = measure_overlay_layout(
        total_height=9,
        footer_max_height=4,
    )

    assert layout.header_height == 1
    assert layout.content_height == 4
    assert layout.footer_height == 4


def test_static_pager_preserves_blank_lines_and_fills_unused_rows() -> None:
    pager = TuiStaticPager(
        get_width=lambda: 10,
        get_height=lambda: 4,
        invalidate=lambda: None,
    )
    pager.open(_request("first", "", "third"))

    assert fragments_text(pager.visible_fragments()) == "first\n\nthird\n~"


def test_runtime_static_pager_reuses_full_screen_lifecycle() -> None:
    runtime = TuiRuntime()
    application = runtime.screen.application

    assert runtime.open_static_pager(_request("diff"))
    assert runtime.screen.static_pager.active
    assert application.full_screen
    assert application.renderer.full_screen

    runtime.close_static_pager()

    assert not runtime.screen.static_pager.active
    assert not application.full_screen
    assert not application.renderer.full_screen
    assert runtime.screen._inline_renderer_state is None


def test_runtime_rejects_static_pager_during_close() -> None:
    runtime = TuiRuntime()
    runtime._closing = True

    assert not runtime.open_static_pager(_request("late diff"))
    assert not runtime.screen.static_pager.active


def test_diff_pager_lines_preserve_ansi_style_and_remove_controls() -> None:
    lines = diff_feature.diff_pager_lines(
        "\x1b[31m-red\x1b[0m\n\x1b]0;unsafe\x07plain"
    )

    assert lines[0] == (("ansired", "-red"),)
    assert fragments_text([fragment for line in lines for fragment in line]) == (
        "-redplain"
    )
    assert "\x1b" not in repr(lines)
    assert "unsafe" not in repr(lines)


@pytest.mark.anyio
async def test_show_workspace_diff_opens_git_result_in_diff_pager(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = SimpleNamespace(open_static_pager=Mock(return_value=True))
    controller = SimpleNamespace(history_workspace=str(tmp_path))
    _DiffService.result = WorkspaceDiffResult(
        WorkspaceDiffState.READY,
        "\x1b[32m+new\x1b[0m\n",
    )
    _DiffService.error = None
    _DiffService.on_compute = None
    monkeypatch.setattr(diff_feature, "WorkspaceDiffService", _DiffService)

    await diff_feature.show_workspace_diff(runtime, controller, cwd=tmp_path)

    request = runtime.open_static_pager.call_args.args[0]
    assert request.title == "D I F F"
    assert request.lines == ((("ansigreen", "+new"),),)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("result", "error", "expected"),
    [
        (
            WorkspaceDiffResult(WorkspaceDiffState.READY, ""),
            None,
            "No changes detected.",
        ),
        (
            WorkspaceDiffResult(WorkspaceDiffState.NOT_GIT_REPOSITORY),
            None,
            "`/diff` \N{EM DASH} _not inside a git repository_",
        ),
        (
            WorkspaceDiffResult(WorkspaceDiffState.READY),
            WorkspaceDiffError("git failed"),
            "Failed to compute diff: git failed",
        ),
    ],
)
async def test_show_workspace_diff_maps_terminal_states_to_pager(
    tmp_path: Path,
    monkeypatch,
    result: WorkspaceDiffResult,
    error: WorkspaceDiffError | None,
    expected: str,
) -> None:
    runtime = SimpleNamespace(open_static_pager=Mock(return_value=True))
    controller = SimpleNamespace(history_workspace=str(tmp_path))
    _DiffService.result = result
    _DiffService.error = error
    _DiffService.on_compute = None
    monkeypatch.setattr(diff_feature, "WorkspaceDiffService", _DiffService)

    await diff_feature.show_workspace_diff(runtime, controller, cwd=tmp_path)

    request = runtime.open_static_pager.call_args.args[0]
    assert fragments_text([
        fragment
        for line in request.lines
        for fragment in line
    ]) == expected


@pytest.mark.anyio
async def test_show_workspace_diff_discards_result_after_cwd_change(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = SimpleNamespace(open_static_pager=Mock(return_value=True))
    controller = SimpleNamespace(history_workspace=str(tmp_path))
    changed = tmp_path / "changed"
    _DiffService.result = WorkspaceDiffResult(
        WorkspaceDiffState.READY,
        "diff",
    )
    _DiffService.error = None
    _DiffService.on_compute = lambda _cwd: setattr(
        controller,
        "history_workspace",
        str(changed),
    )
    monkeypatch.setattr(diff_feature, "WorkspaceDiffService", _DiffService)

    await diff_feature.show_workspace_diff(runtime, controller, cwd=tmp_path)

    runtime.open_static_pager.assert_not_called()


@pytest.mark.anyio
async def test_dispatcher_runs_and_deduplicates_diff_in_background(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = TuiRuntime()
    controller = SimpleNamespace(
        history_workspace=str(tmp_path),
        frontend=SimpleNamespace(
            application=SimpleNamespace(emit=lambda _view: None),
        ),
    )
    started = asyncio.Event()
    release = asyncio.Event()
    show = Mock()

    async def show_diff(received_runtime, received_controller, *, cwd: Path) -> None:
        show(received_runtime, received_controller)
        assert cwd == tmp_path.resolve()
        started.set()
        await release.wait()

    monkeypatch.setattr(dispatch_module, "show_workspace_diff", show_diff)
    dispatcher = TuiCommandDispatcher(
        controller,
        runtime,
        SimpleNamespace(),
        SimpleNamespace(),
    )

    action = await dispatcher.dispatch("/diff")
    await started.wait()
    task = dispatcher._local_tasks["diff"]
    duplicate = await dispatcher.dispatch("/diff")

    assert action is DispatchAction.HANDLED
    assert duplicate is DispatchAction.HANDLED
    assert not task.done()
    show.assert_called_once_with(runtime, controller)

    release.set()
    await task


@pytest.mark.anyio
async def test_stream_diff_runs_as_deduplicated_local_action(monkeypatch) -> None:
    tmp_path = Path.cwd()
    runtime = TuiRuntime()
    controller = SimpleNamespace(
        history_workspace=str(tmp_path),
        frontend=SimpleNamespace(
            application=SimpleNamespace(emit=lambda _view: None),
        ),
    )
    called = Mock()

    async def show_diff(received_runtime, received_controller, *, cwd: Path) -> None:
        called(received_runtime, received_controller)
        assert cwd == tmp_path.resolve()

    monkeypatch.setattr(dispatch_module, "show_workspace_diff", show_diff)
    dispatcher = TuiCommandDispatcher(
        controller,
        runtime,
        SimpleNamespace(),
        SimpleNamespace(),
    )
    cancelled = Mock(return_value=True)

    handled = dispatcher.handle_stream_command("/diff", cancelled)
    await dispatcher._local_tasks["diff"]

    assert handled
    cancelled.assert_not_called()
    called.assert_called_once_with(runtime, controller)


if __name__ == '__main__':
    pass
