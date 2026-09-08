# -*- coding: utf-8 -*-

import asyncio
from collections.abc import Coroutine
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import (
    AsyncMock,
    Mock,
)

import pytest
from prompt_toolkit.keys import Keys
from prompt_toolkit.utils import get_cwidth

from agent.ports import ProtocolCommandClient
from frontends.tui.core.menu import TuiMenu
from frontends.tui.core.models import (
    MenuFooterTone,
    MenuRequest,
    MenuRowDisplay,
    MenuTextInputMode,
)
from frontends.tui.features.review import (
    REVIEW_BRANCH_VIEW_ID,
    REVIEW_COMMIT_VIEW_ID,
    REVIEW_CUSTOM_VIEW_ID,
    REVIEW_FAILURE_VIEW_ID,
    REVIEW_PRESET_VIEW_ID,
    ReviewBranchCatalogValue,
    ReviewCommitValue,
    ReviewMenuController,
    ReviewMenuResult,
    review_target_hint,
)
from frontends.tui.session.dispatch import (
    DispatchAction,
    TuiCommandDispatcher,
)
from infrastructure.platform.git_review import (
    ReviewGitError,
    ReviewGitErrorCode,
)
from protocol.schema.review import (
    ClientReviewWorkspace,
    ReviewBaseBranchTarget,
    ReviewCommitTarget,
    ReviewCustomTarget,
    ReviewUncommittedTarget,
)


@dataclass(frozen=True, slots=True)
class _BranchCatalog:
    current_branch: str | None
    branches: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Commit:
    sha: str
    subject: str


class _Catalog:
    def __init__(self) -> None:
        self.branches: ReviewBranchCatalogValue = _BranchCatalog(
            current_branch="feature/review",
            branches=("main", "release/next"),
        )
        self.commits: tuple[ReviewCommitValue, ...] = (
            _Commit("a" * 40, "Reject stale review terminal events"),
            _Commit("b" * 40, "Preserve review request identity during replay"),
        )
        self.branch_gate: asyncio.Event | None = None
        self.failure: Exception | None = None

    async def branch_catalog(self, cwd: str) -> ReviewBranchCatalogValue:
        assert cwd == "D:/workspace"
        if self.branch_gate is not None:
            await self.branch_gate.wait()
        if self.failure is not None:
            raise self.failure
        return self.branches

    async def recent_commits(
        self,
        cwd: str,
        *,
        limit: int = 100,
    ) -> tuple[ReviewCommitValue, ...]:
        assert cwd == "D:/workspace"
        assert limit == 100
        if self.failure is not None:
            raise self.failure
        return self.commits


class _Runtime:
    def __init__(self, *, width: int = 80) -> None:
        self.menu = TuiMenu(
            invalidate=lambda: None,
            focus_menu=lambda: None,
            focus_input=lambda: None,
            get_width=lambda: width,
        )
        self.tasks: list[asyncio.Task[None]] = []

    async def select_menu(self, request: MenuRequest) -> ReviewMenuResult:
        result = await self.menu.request(request)
        if isinstance(result, (
            ReviewUncommittedTarget,
            ReviewBaseBranchTarget,
            ReviewCommitTarget,
            ReviewCustomTarget,
        )):
            return result
        return None

    def push_menu(self, request: MenuRequest) -> None:
        self.menu.push(request)

    def start_background_task(
        self,
        coroutine: Coroutine[None, None, None],
        *,
        name: str,
    ) -> asyncio.Task[None]:
        task = asyncio.create_task(coroutine, name=name)
        self.tasks.append(task)
        return task

    def active_menu_view_identity(self):
        return self.menu.active_view_identity()

    def menu_session_is_active(self, session_id: int) -> bool:
        return self.menu.session_is_active(session_id)


def _event(key, data: str = ""):
    return SimpleNamespace(
        key=key,
        data=data,
        key_sequence=(SimpleNamespace(key=key),),
    )


def _text(menu: TuiMenu) -> str:
    return "".join(text for _style, text in menu.fragments())


@pytest.mark.anyio
async def test_review_preset_matches_codex_and_returns_uncommitted_target() -> None:
    runtime = _Runtime()
    controller = ReviewMenuController(
        runtime,
        _Catalog(),
        workspace="D:/workspace",
    )
    task = asyncio.create_task(controller.choose())
    await asyncio.sleep(0)

    assert runtime.menu.active_view_id() == REVIEW_PRESET_VIEW_ID
    assert [line.rstrip() for line in _text(runtime.menu).splitlines()] == [
        "  Select a review preset",
        "",
        "› 1. Review against a base branch  (PR Style)",
        "  2. Review uncommitted changes",
        "  3. Review a commit",
        "  4. Custom review instructions",
        "",
        "  Press enter to confirm or esc to go back",
    ]
    assert runtime.menu.state is not None
    request = runtime.menu.state.request
    assert request.footer_tone is MenuFooterTone.SECONDARY
    assert request.row_display is MenuRowDisplay.WRAPPED

    runtime.menu._choose_index(1)
    assert isinstance(await task, ReviewUncommittedTarget)


@pytest.mark.anyio
async def test_review_branch_picker_searches_branch_only_and_returns_parent() -> None:
    runtime = _Runtime()
    controller = ReviewMenuController(
        runtime,
        _Catalog(),
        workspace="D:/workspace",
    )
    task = asyncio.create_task(controller.choose())
    await asyncio.sleep(0)

    runtime.menu._choose_index(0)
    await asyncio.gather(*runtime.tasks)
    assert runtime.menu.active_view_id() == REVIEW_BRANCH_VIEW_ID
    assert runtime.menu.state is not None
    assert runtime.menu.state.request.search_prompt_prefix == ""
    assert [
        option.search_value for option in runtime.menu.state.request.options
    ] == ["main", "release/next"]
    assert [line.rstrip() for line in _text(runtime.menu).splitlines()] == [
        "  Select a base branch",
        "",
        "  Type to search branches",
        "› feature/review -> main",
        "  feature/review -> release/next",
        "",
        "  Press enter to confirm or esc to go back",
    ]

    runtime.menu._update_query("release")
    assert "feature/review -> release/next" in _text(runtime.menu)
    assert "feature/review -> main" not in _text(runtime.menu)
    runtime.menu.cancel()

    assert runtime.menu.active_view_id() == REVIEW_PRESET_VIEW_ID
    assert not task.done()
    runtime.menu.cancel()
    assert await task is None


@pytest.mark.anyio
async def test_review_commit_picker_returns_typed_target_and_closes_parent() -> None:
    runtime = _Runtime()
    controller = ReviewMenuController(
        runtime,
        _Catalog(),
        workspace="D:/workspace",
    )
    task = asyncio.create_task(controller.choose())
    await asyncio.sleep(0)

    runtime.menu._choose_index(2)
    await asyncio.gather(*runtime.tasks)
    assert runtime.menu.active_view_id() == REVIEW_COMMIT_VIEW_ID
    assert runtime.menu.state is not None
    first = runtime.menu.state.request.options[0]
    assert first.label == "Reject stale review terminal events"
    assert first.search_value == (
        f"Reject stale review terminal events {'a' * 40}"
    )
    assert [line.rstrip() for line in _text(runtime.menu).splitlines()] == [
        "  Select a commit to review",
        "",
        "  Type to search commits",
        "› Reject stale review terminal events",
        "  Preserve review request identity during replay",
        "",
        "  Press enter to confirm or esc to go back",
    ]

    runtime.menu._choose_index(0)
    result = await task
    assert result == ReviewCommitTarget(
        sha="a" * 40,
        title="Reject stale review terminal events",
    )
    assert not runtime.menu.active


@pytest.mark.anyio
async def test_review_custom_prompt_preserves_multiline_and_ignores_blank() -> None:
    runtime = _Runtime()
    controller = ReviewMenuController(
        runtime,
        _Catalog(),
        workspace="D:/workspace",
    )
    task = asyncio.create_task(controller.choose())
    await asyncio.sleep(0)

    runtime.menu._choose_index(3)
    assert runtime.menu.active_view_id() == REVIEW_CUSTOM_VIEW_ID
    assert runtime.menu.state is not None
    request = runtime.menu.state.request
    assert request.text_input_mode is MenuTextInputMode.MULTILINE
    assert request.text_input_max_rows == 8
    assert request.surface_style == ""
    assert _text(runtime.menu).splitlines()[:3] == [
        "▌ Custom review instructions",
        "▌",
        "▌ Type instructions and press Enter",
    ]

    assert not runtime.menu.handle_key_event(_event(Keys.Enter))
    assert runtime.menu.active_view_id() == REVIEW_CUSTOM_VIEW_ID
    assert runtime.menu.handle_key_event(
        _event(Keys.BracketedPaste, "  first\r\nsecond  ")
    )
    assert runtime.menu.handle_key_event(_event(Keys.ControlJ))
    assert runtime.menu.handle_key_event(_event("t", "t"))
    assert runtime.menu.handle_key_event(_event(Keys.Enter))

    assert await task == ReviewCustomTarget("first\nsecond  \nt")
    assert not runtime.menu.active


@pytest.mark.anyio
async def test_review_inline_custom_skips_menu() -> None:
    runtime = _Runtime()
    controller = ReviewMenuController(
        runtime,
        _Catalog(),
        workspace="D:/workspace",
    )

    result = await controller.choose(instructions="  focus on races\n  ")

    assert result == ReviewCustomTarget("focus on races")
    assert not runtime.menu.active


@pytest.mark.anyio
async def test_review_stale_catalog_result_cannot_reopen_cancelled_menu() -> None:
    runtime = _Runtime()
    catalog = _Catalog()
    catalog.branch_gate = asyncio.Event()
    controller = ReviewMenuController(
        runtime,
        catalog,
        workspace="D:/workspace",
    )
    task = asyncio.create_task(controller.choose())
    await asyncio.sleep(0)

    runtime.menu._choose_index(0)
    runtime.menu.cancel()
    assert await task is None
    catalog.branch_gate.set()
    await asyncio.gather(*runtime.tasks)

    assert not runtime.menu.active


@pytest.mark.anyio
async def test_review_catalog_failure_child_cancels_back_to_preset() -> None:
    runtime = _Runtime()
    catalog = _Catalog()
    catalog.failure = RuntimeError("Git catalog unavailable")
    controller = ReviewMenuController(
        runtime,
        catalog,
        workspace="D:/workspace",
    )
    task = asyncio.create_task(controller.choose())
    await asyncio.sleep(0)

    runtime.menu._choose_index(2)
    await asyncio.gather(*runtime.tasks)
    assert runtime.menu.active_view_id() == REVIEW_FAILURE_VIEW_ID
    assert [line.rstrip() for line in _text(runtime.menu).splitlines()] == [
        "  Unable to load review targets",
        "  Git catalog unavailable",
        "",
        "  Press enter or esc to close",
    ]
    runtime.menu.cancel()

    assert runtime.menu.active_view_id() == REVIEW_PRESET_VIEW_ID
    assert not task.done()
    runtime.menu.cancel()
    assert await task is None


@pytest.mark.anyio
async def test_review_search_no_matches_keeps_picker_open() -> None:
    runtime = _Runtime()
    controller = ReviewMenuController(
        runtime,
        _Catalog(),
        workspace="D:/workspace",
    )
    task = asyncio.create_task(controller.choose())
    await asyncio.sleep(0)
    runtime.menu._choose_index(2)
    await asyncio.gather(*runtime.tasks)

    runtime.menu._update_query("missing")
    assert [line.rstrip() for line in _text(runtime.menu).splitlines()] == [
        "  Select a commit to review",
        "",
        "  missing",
        "  no matches",
        "",
        "  Press enter to confirm or esc to go back",
    ]
    assert runtime.menu.handle_key_event(_event(Keys.Enter))
    assert runtime.menu.active_view_id() == REVIEW_COMMIT_VIEW_ID

    runtime.menu.cancel()
    runtime.menu.cancel()
    assert await task is None


@pytest.mark.anyio
@pytest.mark.parametrize("width", (40, 80, 120))
async def test_review_wrapped_rows_fit_narrow_wide_and_wide_character_text(
    width: int,
) -> None:
    runtime = _Runtime(width=width)
    catalog = _Catalog()
    catalog.branches = _BranchCatalog(
        current_branch="功能/review",
        branches=("release/这是一个很长的基础分支名称/next",),
    )
    controller = ReviewMenuController(
        runtime,
        catalog,
        workspace="D:/workspace",
    )
    task = asyncio.create_task(controller.choose())
    await asyncio.sleep(0)
    runtime.menu._choose_index(0)
    await asyncio.gather(*runtime.tasks)

    lines = _text(runtime.menu).splitlines()
    assert all(get_cwidth(line) <= width for line in lines)
    assert not any("…" in line for line in lines)

    runtime.menu.cancel()
    runtime.menu.cancel()
    assert await task is None


def test_review_target_hint_uses_the_same_typed_source() -> None:
    assert review_target_hint(ReviewUncommittedTarget()) == "current changes"
    assert review_target_hint(ReviewBaseBranchTarget("main")) == (
        "changes against 'main'"
    )
    assert review_target_hint(ReviewCommitTarget("a" * 40, "Fix race")) == (
        "commit aaaaaaa: Fix race"
    )
    assert review_target_hint(ReviewCustomTarget("Focus on races")) == (
        "Focus on races"
    )


@pytest.mark.anyio
async def test_inline_review_dispatch_freezes_typed_input_without_menu() -> None:
    views = []
    workspace = ClientReviewWorkspace.create()
    snapshot = AsyncMock(return_value=workspace)
    host = SimpleNamespace(
        frontend=SimpleNamespace(
            application=SimpleNamespace(emit=views.append),
        ),
        history_workspace="D:/workspace",
    )
    dispatcher = TuiCommandDispatcher(
        host,
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
        protocol_client=Mock(spec=ProtocolCommandClient),
        review_catalog=_Catalog(),
        review_snapshot=SimpleNamespace(freeze=snapshot),
    )

    action = await dispatcher.dispatch("/review  focus on races  ")
    prepared = dispatcher.take_prepared_review()

    assert action is DispatchAction.REVIEW_TURN
    assert prepared.target == ReviewCustomTarget("focus on races")
    assert prepared.workspace == workspace
    assert prepared.hint == "focus on races"
    snapshot.assert_awaited_once_with(
        "D:/workspace",
        ReviewCustomTarget("focus on races"),
    )


@pytest.mark.anyio
async def test_review_snapshot_limit_is_visible_before_turn_creation() -> None:
    """确保快照超限只产生明确错误且不留下待执行 Review。"""
    views = []
    snapshot = AsyncMock(side_effect=ReviewGitError(
        ReviewGitErrorCode.SNAPSHOT_TOO_LARGE,
        "Review snapshot exceeds the aggregate size limit.",
    ))
    host = SimpleNamespace(
        frontend=SimpleNamespace(
            application=SimpleNamespace(emit=views.append),
        ),
        history_workspace="D:/workspace",
    )
    dispatcher = TuiCommandDispatcher(
        host,
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
        protocol_client=Mock(spec=ProtocolCommandClient),
        review_catalog=_Catalog(),
        review_snapshot=SimpleNamespace(freeze=snapshot),
    )

    action = await dispatcher.dispatch("/review focus on size boundaries")

    assert action is DispatchAction.HANDLED
    failure = next(view for view in views if view.renderable is not None)
    visible = "".join(
        text
        for _style, text in failure.renderable.fragments
    )
    assert visible.endswith(
        "Unable to prepare review: "
        "Review snapshot exceeds the aggregate size limit."
    )
    with pytest.raises(RuntimeError, match="not available"):
        dispatcher.take_prepared_review()
