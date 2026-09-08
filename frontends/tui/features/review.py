# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import enum
import typing
from collections.abc import Coroutine
from dataclasses import dataclass

from agent.application.turns.reviews import review_target_hint
from frontends.tui.contracts.menu import (
    CLOSE_MENU_FOOTER_HINT,
    STANDARD_MENU_FOOTER_HINT,
    MenuColumnWidthMode,
    MenuEmptyAcceptAction,
    MenuFooterTone,
    MenuOption,
    MenuRequest,
    MenuRowDisplay,
    MenuTextInputMode,
)
from frontends.tui.contracts.views import ViewIdentity
from protocol.schema.review import (
    ClientReviewWorkspace,
    ReviewBaseBranchTarget,
    ReviewCommitTarget,
    ReviewCustomTarget,
    ReviewTarget,
    ReviewUncommittedTarget,
)

REVIEW_PRESET_VIEW_ID: typing.Final[str] = "review:preset"
REVIEW_BRANCH_VIEW_ID: typing.Final[str] = "review:base-branch"
REVIEW_COMMIT_VIEW_ID: typing.Final[str] = "review:commit"
REVIEW_CUSTOM_VIEW_ID: typing.Final[str] = "review:custom"
REVIEW_FAILURE_VIEW_ID: typing.Final[str] = "review:catalog-failure"


class ReviewBranchCatalogValue(typing.Protocol):
    """描述 Review 分支选择器需要的稳定目录值。"""

    current_branch: str | None
    branches: tuple[str, ...]


class ReviewCommitValue(typing.Protocol):
    """描述 Review 提交选择器需要的稳定提交摘要。"""

    sha: str
    subject: str


class WorkspaceReviewCatalogPort(typing.Protocol):
    """查询工作区 Review 分支和提交目录，不拥有菜单状态。"""

    async def branch_catalog(self, cwd: str) -> ReviewBranchCatalogValue:
        """返回当前分支和已排序的本地分支。"""
        ...

    async def recent_commits(
        self,
        cwd: str,
        *,
        limit: int = 100,
    ) -> tuple[ReviewCommitValue, ...]:
        """返回最近的不可变提交摘要。"""
        ...


class WorkspaceReviewPreparationPort(typing.Protocol):
    """把 Review 目标冻结为派生事实和规范客户端工作区。"""

    async def freeze(
        self,
        cwd: str,
        target: ReviewTarget,
    ) -> "ResolvedReviewInputValue":
        """返回完整校验后的目标派生事实与客户端工作区。"""
        ...


class ResolvedReviewInputValue(typing.Protocol):
    """描述 Review 准备边界返回的稳定冻结值。"""

    target: ReviewTarget
    workspace: ClientReviewWorkspace


ReviewMenuResult: typing.TypeAlias = ReviewTarget | None


class ReviewMenuRuntimePort(typing.Protocol):
    """定义 Review 菜单控制器使用的 view 栈和任务所有权边界。"""

    async def select_menu(self, request: MenuRequest) -> ReviewMenuResult:
        """打开根菜单并返回唯一目标或取消结果。"""
        ...

    def push_menu(self, request: MenuRequest) -> None:
        """在当前菜单会话中压入子视图。"""
        ...

    def start_background_task(
        self,
        coroutine: Coroutine[None, None, None],
        *,
        name: str,
    ) -> asyncio.Task[None]:
        """托管与 TUI 生命周期一致的目录查询任务。"""
        ...

    def active_menu_view_identity(self) -> ViewIdentity | None:
        """返回当前栈顶菜单的稳定身份。"""
        ...

    def menu_session_is_active(self, session_id: int) -> bool:
        """返回菜单会话是否仍可接收异步结果。"""
        ...


class _ReviewNavigation(enum.Enum):
    """标识根预设菜单内部的子视图导航。"""

    BASE_BRANCH = "base_branch"
    COMMIT = "commit"
    CUSTOM = "custom"


@dataclass(frozen=True, slots=True)
class PreparedReview:
    """保存菜单完成后、远端身份创建前的冻结 Review 输入。"""

    target: ReviewTarget
    workspace: ClientReviewWorkspace
    hint: str


class ReviewMenuController:
    """组装一次 Review 目标选择会话及其异步目录导航。"""

    def __init__(
        self,
        runtime: ReviewMenuRuntimePort,
        catalog: WorkspaceReviewCatalogPort,
        *,
        workspace: str,
    ) -> None:
        """绑定菜单运行时、只读目录端口和工作区。"""
        self._runtime = runtime
        self._catalog = catalog
        self._workspace = workspace
        self._catalog_generation = 0

    async def choose(self, *, instructions: str = "") -> ReviewMenuResult:
        """按行内指令或预设菜单返回一个类型化 Review 目标。"""
        normalized = instructions.strip()
        if normalized:
            return ReviewCustomTarget(instructions=normalized)
        result = await self._runtime.select_menu(self._preset_request())
        return result if _is_review_target(result) else None

    def _preset_request(self) -> MenuRequest:
        """返回与 Codex 排列和文案一致的 Review 预设菜单。"""
        return MenuRequest(
            title="Select a review preset",
            view_id=REVIEW_PRESET_VIEW_ID,
            footer_hint=STANDARD_MENU_FOOTER_HINT,
            footer_tone=MenuFooterTone.SECONDARY,
            row_display=MenuRowDisplay.WRAPPED,
            column_width_mode=MenuColumnWidthMode.AUTO_VISIBLE,
            options=(
                MenuOption(
                    _ReviewNavigation.BASE_BRANCH,
                    "Review against a base branch",
                    "(PR Style)",
                    on_select=lambda: self._start_catalog_load("branch"),
                    dismiss_on_select=False,
                    dismiss_parent_on_child_accept=True,
                ),
                MenuOption(
                    ReviewUncommittedTarget(),
                    "Review uncommitted changes",
                ),
                MenuOption(
                    _ReviewNavigation.COMMIT,
                    "Review a commit",
                    on_select=lambda: self._start_catalog_load("commit"),
                    dismiss_on_select=False,
                    dismiss_parent_on_child_accept=True,
                ),
                MenuOption(
                    _ReviewNavigation.CUSTOM,
                    "Custom review instructions",
                    on_select=self._open_custom_prompt,
                    dismiss_on_select=False,
                    dismiss_parent_on_child_accept=True,
                ),
            ),
        )

    def _open_custom_prompt(self) -> None:
        """压入由共享菜单编辑器拥有的多行自定义指令视图。"""
        self._runtime.push_menu(MenuRequest(
            title="Custom review instructions",
            view_id=REVIEW_CUSTOM_VIEW_ID,
            surface_style="",
            surface_horizontal_inset=0,
            text_input_mode=MenuTextInputMode.MULTILINE,
            text_input_max_rows=8,
            text_input_result_factory=ReviewCustomTarget,
            text_input_gutter="▌",
            search_placeholder="Type instructions and press Enter",
            empty_accept_action=MenuEmptyAcceptAction.SUBMIT_QUERY,
            footer_hint=STANDARD_MENU_FOOTER_HINT,
            show_option_gutter=False,
            separate_options=False,
        ))

    def _start_catalog_load(self, kind: typing.Literal["branch", "commit"]) -> None:
        """启动一个只允许最新根菜单身份接收结果的目录查询。"""
        identity = self._runtime.active_menu_view_identity()
        if identity is None or identity.view_id != REVIEW_PRESET_VIEW_ID:
            return None
        session_id = identity.session_id
        if session_id is None:
            return None
        self._catalog_generation += 1
        catalog_generation = self._catalog_generation
        operation = (
            self._load_branches(
                session_id,
                identity.generation,
                catalog_generation,
            )
            if kind == "branch"
            else self._load_commits(
                session_id,
                identity.generation,
                catalog_generation,
            )
        )
        self._runtime.start_background_task(
            operation,
            name=f"review {kind} catalog",
        )

    async def _load_branches(
        self,
        session_id: int,
        view_generation: int,
        catalog_generation: int,
    ) -> None:
        """查询本地分支并在身份仍匹配时压入搜索选择器。"""
        try:
            catalog = await self._catalog.branch_catalog(self._workspace)
        except Exception as error:
            self._push_catalog_failure(
                error,
                session_id=session_id,
                view_generation=view_generation,
                catalog_generation=catalog_generation,
            )
            return None
        if not self._catalog_result_is_current(
            session_id,
            view_generation,
            catalog_generation,
        ):
            return None
        current = catalog.current_branch or "(detached HEAD)"
        self._runtime.push_menu(MenuRequest(
            title="Select a base branch",
            view_id=REVIEW_BRANCH_VIEW_ID,
            searchable=True,
            search_prompt_prefix="",
            search_placeholder="Type to search branches",
            search_empty_text="no matches",
            empty_accept_action=MenuEmptyAcceptAction.IGNORE,
            footer_hint=STANDARD_MENU_FOOTER_HINT,
            footer_tone=MenuFooterTone.SECONDARY,
            row_display=MenuRowDisplay.WRAPPED,
            column_width_mode=MenuColumnWidthMode.AUTO_VISIBLE,
            options=tuple(
                MenuOption(
                    ReviewBaseBranchTarget(branch=branch),
                    f"{current} -> {branch}",
                    search_value=branch,
                )
                for branch in catalog.branches
            ),
        ))

    async def _load_commits(
        self,
        session_id: int,
        view_generation: int,
        catalog_generation: int,
    ) -> None:
        """查询最近提交并在身份仍匹配时压入搜索选择器。"""
        try:
            commits = await self._catalog.recent_commits(
                self._workspace,
                limit=100,
            )
        except Exception as error:
            self._push_catalog_failure(
                error,
                session_id=session_id,
                view_generation=view_generation,
                catalog_generation=catalog_generation,
            )
            return None
        if not self._catalog_result_is_current(
            session_id,
            view_generation,
            catalog_generation,
        ):
            return None
        self._runtime.push_menu(MenuRequest(
            title="Select a commit to review",
            view_id=REVIEW_COMMIT_VIEW_ID,
            searchable=True,
            search_prompt_prefix="",
            search_placeholder="Type to search commits",
            search_empty_text="no matches",
            empty_accept_action=MenuEmptyAcceptAction.IGNORE,
            footer_hint=STANDARD_MENU_FOOTER_HINT,
            footer_tone=MenuFooterTone.SECONDARY,
            row_display=MenuRowDisplay.WRAPPED,
            column_width_mode=MenuColumnWidthMode.AUTO_VISIBLE,
            options=tuple(
                MenuOption(
                    ReviewCommitTarget(
                        sha=commit.sha,
                        title=commit.subject,
                    ),
                    commit.subject,
                    search_value=f"{commit.subject} {commit.sha}",
                )
                for commit in commits
            ),
        ))

    def _push_catalog_failure(
        self,
        error: Exception,
        *,
        session_id: int,
        view_generation: int,
        catalog_generation: int,
    ) -> None:
        """在目录查询仍归属当前根菜单时压入可返回的失败子视图。"""
        if not self._catalog_result_is_current(
            session_id,
            view_generation,
            catalog_generation,
        ):
            return None
        message = str(error).strip() or "Review targets are unavailable."
        self._runtime.push_menu(MenuRequest(
            title="Unable to load review targets",
            view_id=REVIEW_FAILURE_VIEW_ID,
            body=(message,),
            empty_accept_action=MenuEmptyAcceptAction.CANCEL,
            footer_hint=CLOSE_MENU_FOOTER_HINT,
            footer_tone=MenuFooterTone.SECONDARY,
        ))

    def _catalog_result_is_current(
        self,
        session_id: int,
        view_generation: int,
        catalog_generation: int,
    ) -> bool:
        """校验后台结果仍属于当前根菜单和最新目录操作。"""
        if (
            catalog_generation != self._catalog_generation
            or not self._runtime.menu_session_is_active(session_id)
        ):
            return False
        identity = self._runtime.active_menu_view_identity()
        return bool(
            identity is not None
            and identity.view_id == REVIEW_PRESET_VIEW_ID
            and identity.generation == view_generation
            and identity.session_id == session_id
        )


def _is_review_target(value: ReviewMenuResult) -> bool:
    """返回菜单结果是否为正式 Review 目标。"""
    return isinstance(value, (
        ReviewUncommittedTarget,
        ReviewBaseBranchTarget,
        ReviewCommitTarget,
        ReviewCustomTarget,
    ))


if __name__ == '__main__':
    pass
