# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import typing
from dataclasses import (
    dataclass,
    replace,
)

from agent.ports import (
    ApprovalCompleted,
    ApprovalReviewCompleted,
    ApprovalReviewStarted,
    ApprovalStarted,
    AssistantBuffered,
    AssistantSettled,
    AssistantVisible,
    ModelWaitReason,
    ModelWaitRequested,
    OutputActivityEvent,
    OutputActivityPort,
    OutputSurfaceContext,
    PresentationSuperseded,
    RecoveryActivityMode,
    RecoveryChanged,
    ResponseIdentity,
    RetryActivitySource,
    RetryChanged,
    SurfaceClosed,
    SurfaceTurnStarted,
    TerminalWaitCompleted,
    TerminalWaitStarted,
    ToolActivityKind,
    ToolBatchCompleted,
    ToolBatchStarted,
    ToolCompleted,
    ToolStarted,
    TurnTerminal,
)

SurfaceLifecycle = typing.Literal[
    "inactive",
    "active",
    "terminal",
    "closed",
]

SurfaceContentState = typing.Literal[
    "none",
    "buffered",
    "visible",
    "settled",
]

SurfaceIndicatorKind = typing.Literal[
    "hidden",
    "reviewing",
    "thinking",
    "retrying",
    "terminal",
]


@dataclass(frozen=True, slots=True)
class AssistantActivity:
    """保存当前表面已知的一项 assistant 正文身份。"""

    identity: ResponseIdentity
    item_id: str


@dataclass(frozen=True, slots=True)
class ToolActivity:
    """保存一个活动工具 lease 的类型、身份和名称。"""

    tool_id: str
    tool_kind: ToolActivityKind
    name: str


@dataclass(frozen=True, slots=True)
class TerminalWaitActivity:
    """保存一个后台终端等待 lease。"""

    call_id: str
    session_id: str
    command: str


@dataclass(frozen=True, slots=True)
class ApprovalActivity:
    """保存一个独占审批表面的稳定身份。"""

    approval_id: str
    call_id: str


@dataclass(frozen=True, slots=True)
class ApprovalReviewActivity:
    """保存一个自动评审 lease 的身份、代次和动作摘要。"""

    review_id: str
    approval_id: str
    call_id: str
    action_summary: str
    presentation_epoch: int


@dataclass(frozen=True, slots=True)
class RetryActivity:
    """保存一个重试来源及其所属 provider Attempt。"""

    source: RetryActivitySource
    presentation_epoch: int
    round: int
    attempt: int


@dataclass(frozen=True, slots=True)
class PresentationReplacement:
    """保存一个已经确认的 Worker 展示代次替换边界。"""

    superseded_epoch: int
    presentation_epoch: int


@dataclass(frozen=True, slots=True)
class TurnSurfaceState:
    """保存纯 reducer 使用的正交展示状态。"""

    context: OutputSurfaceContext
    lifecycle: SurfaceLifecycle = "inactive"
    content: SurfaceContentState = "none"
    buffered_items: tuple[AssistantActivity, ...] = ()
    settled_items: tuple[AssistantActivity, ...] = ()
    presentation_replacements: tuple[PresentationReplacement, ...] = ()
    provider_retry_boundaries: tuple[RetryActivity, ...] = ()
    visible_item: AssistantActivity | None = None
    model_wait_revision: int | None = None
    model_wait_reason: ModelWaitReason | None = None
    batches: tuple[str, ...] = ()
    completed_batches: tuple[str, ...] = ()
    tools: tuple[ToolActivity, ...] = ()
    completed_tools: tuple[ToolActivity, ...] = ()
    terminal_waits: tuple[TerminalWaitActivity, ...] = ()
    completed_terminal_waits: tuple[TerminalWaitActivity, ...] = ()
    approvals: tuple[ApprovalActivity, ...] = ()
    completed_approvals: tuple[ApprovalActivity, ...] = ()
    approval_reviews: tuple[ApprovalReviewActivity, ...] = ()
    completed_approval_reviews: tuple[ApprovalReviewActivity, ...] = ()
    retries: tuple[RetryActivity, ...] = ()
    completed_retries: tuple[RetryActivity, ...] = ()
    recovery: RecoveryActivityMode = "live"
    recovery_event_seq: int = 0
    terminal_status: str = ""
    revision: int = 0


@dataclass(frozen=True, slots=True)
class SurfaceProjection:
    """描述 TUI 活动区域从 reducer 状态派生的唯一投影。"""

    indicator: SurfaceIndicatorKind
    title: str = ""
    detail: str = ""
    revision: int = 0


@dataclass(frozen=True, slots=True)
class TurnSurfaceTiming:
    """定义 TUI 本地等待恢复策略，协议事件不得携带这些时间值。"""

    tool_result_sec: float = 0.15
    lifecycle_sec: float = 0.15
    tool_started_sec: float = 0.12
    transport_retry_min_visible_sec: float = 0.8

    def delay_for(self, reason: ModelWaitReason | None) -> float:
        """返回指定等待来源的非负本地延时。"""
        if reason == "tool_result":
            return max(0.0, self.tool_result_sec)
        if reason == "lifecycle":
            return max(0.0, self.lifecycle_sec)
        return 0.0

    def delay_for_projection(
        self,
        projection: SurfaceProjection,
        *,
        state: TurnSurfaceState,
    ) -> float:
        """返回一项派生表面投影的本地抑制时间。"""
        if (
            projection.indicator == "thinking"
            and (state.tools or state.batches)
        ):
            return max(0.0, self.tool_started_sec)
        if projection.indicator == "thinking":
            return self.delay_for(state.model_wait_reason)
        return 0.0


def initial_turn_surface_state(
    context: OutputSurfaceContext,
) -> TurnSurfaceState:
    """创建尚未开始观察事件的空展示状态。"""
    if not isinstance(context, OutputSurfaceContext):
        raise TypeError("output surface context is required")
    return TurnSurfaceState(context=context)


def reduce_turn_surface(
    state: TurnSurfaceState,
    event: OutputActivityEvent,
) -> TurnSurfaceState:
    """按稳定身份和顺序把一项展示事实归约为新状态。"""
    if not isinstance(state, TurnSurfaceState):
        raise TypeError("turn surface state is required")
    _require_scope(state.context, event)

    if state.lifecycle == "closed":
        if isinstance(event, SurfaceClosed):
            return state
        raise RuntimeError("turn surface is closed")

    updated = _reduce_active_surface(state, event)
    if updated == state:
        return state
    return replace(updated, revision=state.revision + 1)


def project_turn_surface(state: TurnSurfaceState) -> SurfaceProjection:
    """从正交状态派生单一活动区域投影。"""
    revision = state.revision
    if state.lifecycle in {"inactive", "terminal", "closed"}:
        return SurfaceProjection("hidden", revision=revision)
    if state.recovery in {"replaying", "gap"}:
        return SurfaceProjection("hidden", revision=revision)
    if state.approvals:
        return SurfaceProjection("hidden", revision=revision)
    if any(item.source == "transport" for item in state.retries):
        return SurfaceProjection(
            "retrying",
            title="Retrying",
            detail="transport",
            revision=revision,
        )
    if state.content == "visible":
        return SurfaceProjection("hidden", revision=revision)
    if state.retries:
        return SurfaceProjection(
            "retrying",
            title="Retrying",
            detail="provider",
            revision=revision,
        )
    if state.approval_reviews:
        review_count = len(state.approval_reviews)
        title = (
            "Reviewing approval request"
            if review_count == 1
            else f"Reviewing {review_count} approval requests"
        )
        details = [
            review.action_summary
            for review in state.approval_reviews[:3]
        ]
        remaining = review_count - len(details)
        if remaining > 0:
            details.append(f"+{remaining} more")
        return SurfaceProjection(
            "reviewing",
            title=title,
            detail="\n".join(details),
            revision=revision,
        )
    if state.terminal_waits:
        wait = state.terminal_waits[-1]
        return SurfaceProjection(
            "terminal",
            title="Waiting for background terminal",
            detail=wait.command,
            revision=revision,
        )
    if state.tools or state.batches:
        return SurfaceProjection(
            "thinking",
            title="Thinking",
            revision=revision,
        )
    if state.model_wait_revision is not None:
        return SurfaceProjection(
            "thinking",
            title="Thinking",
            revision=revision,
        )
    return SurfaceProjection("hidden", revision=revision)


ApplySurfaceProjection = typing.Callable[
    [SurfaceProjection],
    typing.Awaitable[None],
]
ApplyImmediateSurfaceProjection = typing.Callable[[SurfaceProjection], None]


class TuiTurnSurfaceCoordinator(OutputActivityPort):
    """串行化单个 TUI OutputSession 的 reducer、timer 和视觉投影。"""

    def __init__(
        self,
        context: OutputSurfaceContext,
        apply_projection: ApplySurfaceProjection,
        *,
        apply_immediate_projection: (
            ApplyImmediateSurfaceProjection | None
        ) = None,
        timing: TurnSurfaceTiming = TurnSurfaceTiming(),
    ) -> None:
        """绑定不可变 scope、投影出口和本地时间策略。"""
        if not callable(apply_projection):
            raise TypeError("turn surface projection sink is required")
        self.context = context
        self.state = initial_turn_surface_state(context)
        self.apply_projection = apply_projection
        self.apply_immediate_projection = apply_immediate_projection
        self.timing = timing
        self._applied: SurfaceProjection | None = None
        self._timer: asyncio.Task[None] | None = None
        self._timer_error: BaseException | None = None
        self._transport_retry_visible_until: float = 0.0
        self._opened: bool = False
        self._closed: bool = False
        self._lock: asyncio.Lock = asyncio.Lock()

    @property
    def pending_timer(self) -> bool:
        """返回当前是否存在尚未完成的延迟投影任务。"""
        return self._timer is not None and not self._timer.done()

    async def open(self) -> None:
        """启动当前表面但不抢占尚未迁入的既有视觉状态。"""
        async with self._lock:
            if self._closed:
                raise RuntimeError("turn surface coordinator is closed")
            if self._opened:
                return None
            self._opened = True
            self.state = reduce_turn_surface(
                self.state,
                SurfaceTurnStarted(
                    surface_id=self.context.surface_id,
                    turn_id=self.context.turn_id,
                ),
            )

    async def emit(self, event: OutputActivityEvent) -> None:
        """取消陈旧 timer、归约事实并提交或调度唯一派生投影。"""
        async with self._lock:
            self._raise_timer_error()
            if not self._opened:
                raise RuntimeError("turn surface coordinator is not open")
            if self._closed:
                raise RuntimeError("turn surface coordinator is closed")
            self._cancel_timer()
            self.state = reduce_turn_surface(self.state, event)
            projection = project_turn_surface(self.state)
            delay = self.timing.delay_for_projection(
                projection,
                state=self.state,
            )
            if (
                isinstance(event, (ApprovalStarted, TurnTerminal, SurfaceClosed))
                or (
                    isinstance(event, RecoveryChanged)
                    and event.mode in {"replaying", "gap"}
                )
            ):
                self._transport_retry_visible_until = 0.0
            elif not (
                projection.indicator == "retrying"
                and projection.detail == "transport"
            ):
                delay = max(delay, self._transport_retry_remaining())
            if (
                projection.indicator == "thinking"
                and (self.state.tools or self.state.batches)
                and isinstance(
                    event,
                    (ApprovalCompleted, TerminalWaitCompleted, ToolCompleted),
                )
            ):
                delay = 0.0
            if delay > 0:
                self._timer = asyncio.create_task(
                    self._apply_after(
                        projection,
                        expected_revision=self.state.revision,
                        delay=delay,
                    ),
                    name=f"tui surface {self.context.surface_id}",
                )
                return None
            await self._apply(projection)

    def emit_assistant_visible(self, event: AssistantVisible) -> None:
        """在正文画布事务内同步取消 timer 并提交可见性投影。"""
        self._raise_timer_error()
        if not self._opened:
            raise RuntimeError("turn surface coordinator is not open")
        if self._closed:
            raise RuntimeError("turn surface coordinator is closed")
        if self.apply_immediate_projection is None:
            raise RuntimeError("immediate turn surface projection sink is required")

        self._cancel_timer()
        self._transport_retry_visible_until = 0.0
        previous = self.state
        self.state = reduce_turn_surface(self.state, event)
        if self.state is previous:
            return None

        projection = project_turn_surface(self.state)
        if projection.indicator != "hidden":
            raise RuntimeError("visible assistant must own the activity surface")
        self.apply_immediate_projection(projection)
        self._applied = projection

    async def close(self) -> None:
        """幂等取消 timer 并使当前表面的全部后续事件失效。"""
        timer: asyncio.Task[None] | None = None
        projection_error: BaseException | None = None
        async with self._lock:
            if self._closed:
                self._raise_timer_error()
                return None
            self._closed = True
            timer = self._timer
            self._cancel_timer()
            if self._opened:
                self.state = reduce_turn_surface(
                    self.state,
                    SurfaceClosed(
                        surface_id=self.context.surface_id,
                        turn_id=self.context.turn_id,
                    ),
                )
                try:
                    await self._apply(project_turn_surface(self.state))
                except BaseException as error:
                    projection_error = error
        if timer is not None:
            await asyncio.gather(timer, return_exceptions=True)
        self._raise_timer_error()
        if projection_error is not None:
            raise projection_error

    async def _apply_after(
        self,
        projection: SurfaceProjection,
        *,
        expected_revision: int,
        delay: float,
    ) -> None:
        """仅在 reducer revision 未变化时提交延迟投影。"""
        task = asyncio.current_task()
        try:
            await asyncio.sleep(delay)
            async with self._lock:
                if (
                    self._closed
                    or self.state.revision != expected_revision
                    or project_turn_surface(self.state) != projection
                ):
                    return None
                await self._apply(projection)
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            self._timer_error = error
        finally:
            if self._timer is task:
                self._timer = None

    async def _apply(self, projection: SurfaceProjection) -> None:
        """提交投影，并在同步正文交接抢占后恢复最新 reducer 结果。"""
        candidate = projection
        while self._applied != candidate:
            previous = self._applied
            await self.apply_projection(candidate)
            current = project_turn_surface(self.state)
            if current != candidate:
                if (
                    current.indicator == "hidden"
                    and self.apply_immediate_projection is not None
                ):
                    self.apply_immediate_projection(current)
                    self._applied = current
                    return None
                candidate = current
                continue
            self._applied = candidate
            if (
                candidate.indicator == "retrying"
                and candidate.detail == "transport"
                and not (
                    previous is not None
                    and previous.indicator == "retrying"
                    and previous.detail == "transport"
                )
            ):
                self._transport_retry_visible_until = (
                    asyncio.get_running_loop().time()
                    + max(0.0, self.timing.transport_retry_min_visible_sec)
                )

    def _transport_retry_remaining(self) -> float:
        """返回 transport retry 已展示帧的剩余最短可见时间。"""
        return max(
            0.0,
            self._transport_retry_visible_until
            - asyncio.get_running_loop().time(),
        )

    def _cancel_timer(self) -> None:
        """同步取消当前 generation timer。"""
        timer = self._timer
        self._timer = None
        if timer is not None and not timer.done():
            timer.cancel()

    def _raise_timer_error(self) -> None:
        """把延迟视觉出口的失败交还输出会话生命周期。"""
        error = self._timer_error
        self._timer_error = None
        if error is not None:
            raise error


def _reduce_active_surface(
    state: TurnSurfaceState,
    event: OutputActivityEvent,
) -> TurnSurfaceState:
    """处理已经通过 scope 校验的一项事件。"""
    if isinstance(event, SurfaceTurnStarted):
        if state.lifecycle == "inactive":
            return replace(state, lifecycle="active")
        return state
    if state.lifecycle == "terminal":
        if isinstance(event, TurnTerminal):
            if event.status != state.terminal_status:
                raise ValueError("turn terminal status conflicts with existing state")
            return state
        if isinstance(event, SurfaceClosed):
            return replace(state, lifecycle="closed")
        return state
    if state.lifecycle != "active" and not isinstance(event, SurfaceClosed):
        raise RuntimeError("turn surface is not active")
    if isinstance(event, ModelWaitRequested):
        if state.model_wait_revision is not None:
            if event.revision < state.model_wait_revision:
                return state
            if event.revision == state.model_wait_revision:
                if event.reason != state.model_wait_reason:
                    raise ValueError("model wait revision was reused")
                return state
        return replace(
            state,
            model_wait_revision=event.revision,
            model_wait_reason=event.reason,
        )
    if isinstance(event, AssistantBuffered):
        item = _assistant_activity(event.identity, event.item_id)
        if _assistant_is_superseded(state, item):
            return state
        if item in state.buffered_items or item in state.settled_items:
            return state
        return replace(
            state,
            content="buffered",
            buffered_items=(*state.buffered_items, item),
        )
    if isinstance(event, AssistantVisible):
        item = _assistant_activity(event.identity, event.item_id)
        if _assistant_is_superseded(state, item):
            return state
        if item in state.settled_items:
            return state
        if item not in state.buffered_items:
            raise ValueError("visible assistant item was not buffered")
        return replace(
            state,
            content="visible",
            visible_item=item,
            model_wait_revision=None,
            model_wait_reason=None,
        )
    if isinstance(event, AssistantSettled):
        item = _assistant_activity(event.identity, event.item_id)
        if _assistant_is_superseded(state, item):
            return state
        if item in state.settled_items:
            return state
        if item not in state.buffered_items:
            raise ValueError("settled assistant item was not buffered")
        if state.visible_item not in {None, item}:
            raise ValueError("settled assistant item does not match visible item")
        return replace(
            state,
            content="settled",
            settled_items=(*state.settled_items, item),
            visible_item=None,
        )
    if isinstance(event, PresentationSuperseded):
        replacement = PresentationReplacement(
            event.superseded_epoch,
            event.presentation_epoch,
        )
        existing = next(
            (
                item
                for item in state.presentation_replacements
                if item.superseded_epoch == event.superseded_epoch
            ),
            None,
        )
        if existing is not None:
            if existing != replacement:
                raise ValueError("presentation supersede identity was reused")
            return state
        updated = replace(
            state,
            presentation_replacements=(
                *state.presentation_replacements,
                replacement,
            ),
            model_wait_revision=None,
            model_wait_reason=None,
            retries=tuple(
                retry
                for retry in state.retries
                if retry.presentation_epoch != event.superseded_epoch
            ),
            approval_reviews=tuple(
                review
                for review in state.approval_reviews
                if review.presentation_epoch > event.superseded_epoch
            ),
        )
        return _clear_superseded_content(updated)
    if isinstance(event, ToolBatchStarted):
        if (
            event.batch_id in state.batches
            or event.batch_id in state.completed_batches
        ):
            return state
        return replace(state, batches=(*state.batches, event.batch_id))
    if isinstance(event, ToolBatchCompleted):
        if event.batch_id not in state.batches:
            if event.batch_id in state.completed_batches:
                return state
            raise ValueError("tool batch completion does not match active batch")
        return replace(
            state,
            batches=tuple(
                batch_id
                for batch_id in state.batches
                if batch_id != event.batch_id
            ),
            completed_batches=(*state.completed_batches, event.batch_id),
        )
    if isinstance(event, ToolStarted):
        tool = ToolActivity(event.tool_id, event.tool_kind, event.name)
        existing = _tool_by_id(state.tools, event.tool_id)
        completed = _tool_by_id(state.completed_tools, event.tool_id)
        if existing is not None:
            if existing != tool:
                raise ValueError("tool activity identity was reused")
            return state
        if completed is not None:
            if completed != tool:
                raise ValueError("tool activity identity was reused")
            return state
        return replace(state, tools=(*state.tools, tool))
    if isinstance(event, ToolCompleted):
        tool = _tool_by_id(state.tools, event.tool_id)
        if tool is None:
            completed = _tool_by_id(state.completed_tools, event.tool_id)
            if completed is not None and (
                completed.tool_kind == event.tool_kind
                and (not event.name or completed.name == event.name)
            ):
                return state
            raise ValueError("tool completion does not match active tool")
        if tool.tool_kind != event.tool_kind or (
            event.name and tool.name != event.name
        ):
            raise ValueError("tool completion identity does not match active tool")
        return replace(
            state,
            tools=tuple(item for item in state.tools if item.tool_id != event.tool_id),
            completed_tools=(*state.completed_tools, tool),
        )
    if isinstance(event, TerminalWaitStarted):
        wait = TerminalWaitActivity(event.call_id, event.session_id, event.command)
        existing = _terminal_wait_by_identity(
            state.terminal_waits,
            event.call_id,
            event.session_id,
        )
        completed = _terminal_wait_by_identity(
            state.completed_terminal_waits,
            event.call_id,
            event.session_id,
        )
        if existing is not None:
            if existing != wait:
                raise ValueError("terminal wait identity was reused")
            return state
        if completed is not None:
            if completed != wait:
                raise ValueError("terminal wait identity was reused")
            return state
        return replace(state, terminal_waits=(*state.terminal_waits, wait))
    if isinstance(event, TerminalWaitCompleted):
        existing = _terminal_wait_by_identity(
            state.terminal_waits,
            event.call_id,
            event.session_id,
        )
        if existing is None:
            completed = _terminal_wait_by_identity(
                state.completed_terminal_waits,
                event.call_id,
                event.session_id,
            )
            if completed is not None:
                return state
            raise ValueError("terminal wait completion does not match active wait")
        return replace(
            state,
            terminal_waits=tuple(
                item
                for item in state.terminal_waits
                if (item.call_id, item.session_id)
                != (event.call_id, event.session_id)
            ),
            completed_terminal_waits=(
                *state.completed_terminal_waits,
                existing,
            ),
        )
    if isinstance(event, ApprovalStarted):
        approval = ApprovalActivity(event.approval_id, event.call_id)
        if approval in state.approvals or approval in state.completed_approvals:
            return state
        return replace(state, approvals=(*state.approvals, approval))
    if isinstance(event, ApprovalCompleted):
        approval = ApprovalActivity(event.approval_id, event.call_id)
        if approval not in state.approvals:
            if approval in state.completed_approvals:
                return state
            raise ValueError("approval completion does not match active approval")
        return replace(
            state,
            approvals=tuple(item for item in state.approvals if item != approval),
            completed_approvals=(*state.completed_approvals, approval),
        )
    if isinstance(event, ApprovalReviewStarted):
        review = _approval_review_activity(event)
        existing = _approval_review_by_id(
            state.approval_reviews,
            event.review_id,
        )
        completed = _approval_review_by_id(
            state.completed_approval_reviews,
            event.review_id,
        )
        if existing is not None:
            if existing != review:
                raise ValueError("approval review identity was reused")
            return state
        if completed is not None:
            if completed != review:
                raise ValueError("approval review identity was reused")
            return state
        if _presentation_epoch_is_superseded(
            state,
            event.presentation_epoch,
        ):
            return state
        return replace(
            state,
            approval_reviews=(*state.approval_reviews, review),
        )
    if isinstance(event, ApprovalReviewCompleted):
        review = _approval_review_activity(event)
        existing = _approval_review_by_id(
            state.approval_reviews,
            event.review_id,
        )
        if existing is None:
            completed = _approval_review_by_id(
                state.completed_approval_reviews,
                event.review_id,
            )
            if completed is not None:
                if completed != review:
                    raise ValueError("approval review identity was reused")
                return state
            return replace(
                state,
                completed_approval_reviews=(
                    *state.completed_approval_reviews,
                    review,
                ),
            )
        if existing != review:
            raise ValueError("approval review completion identity does not match")
        return replace(
            state,
            approval_reviews=tuple(
                item
                for item in state.approval_reviews
                if item.review_id != event.review_id
            ),
            completed_approval_reviews=(
                *state.completed_approval_reviews,
                review,
            ),
        )
    if isinstance(event, RetryChanged):
        retry = RetryActivity(
            event.source,
            event.presentation_epoch,
            event.round,
            event.attempt,
        )
        if event.state == "started":
            if _presentation_epoch_is_superseded(
                state,
                event.presentation_epoch,
            ):
                return state
            if retry in state.completed_retries:
                return state
            provider_boundary = next(
                (
                    item
                    for item in state.provider_retry_boundaries
                    if item.presentation_epoch == event.presentation_epoch
                    and item.round == event.round
                ),
                None,
            )
            if (
                event.source == "provider"
                and provider_boundary is not None
                and provider_boundary.attempt > event.attempt
            ):
                return state
            active = next(
                (
                    item
                    for item in state.retries
                    if item.source == event.source
                ),
                None,
            )
            if active is not None and _retry_order(active) > _retry_order(retry):
                return state
            without_source = tuple(
                item for item in state.retries if item.source != event.source
            )
            updated = replace(state, retries=(*without_source, retry))
            if event.source != "provider":
                return updated
            existing_boundaries = tuple(
                item
                for item in state.provider_retry_boundaries
                if (
                    item.presentation_epoch == event.presentation_epoch
                    and item.round == event.round
                )
            )
            if not existing_boundaries or all(
                item.attempt < event.attempt
                for item in existing_boundaries
            ):
                updated = replace(
                    updated,
                    provider_retry_boundaries=(
                        *(
                            item
                            for item in state.provider_retry_boundaries
                            if not (
                                item.presentation_epoch == event.presentation_epoch
                                and item.round == event.round
                            )
                        ),
                        retry,
                    ),
                )
            return _clear_superseded_content(updated)
        matching = tuple(
            item
            for item in state.retries
            if item.source == event.source
            and item.presentation_epoch == event.presentation_epoch
            and item.round == event.round
            and item.attempt == event.attempt
        )
        if not matching:
            return state
        return replace(
            state,
            retries=tuple(item for item in state.retries if item not in matching),
            completed_retries=(*state.completed_retries, retry),
        )
    if isinstance(event, RecoveryChanged):
        if event.event_seq < state.recovery_event_seq:
            return state
        return replace(
            state,
            recovery=event.mode,
            recovery_event_seq=event.event_seq,
        )
    if isinstance(event, TurnTerminal):
        return replace(
            state,
            lifecycle="terminal",
            content=("settled" if state.buffered_items else "none"),
            visible_item=None,
            model_wait_revision=None,
            model_wait_reason=None,
            batches=(),
            tools=(),
            terminal_waits=(),
            approvals=(),
            approval_reviews=(),
            retries=(),
            terminal_status=event.status,
        )
    if isinstance(event, SurfaceClosed):
        return replace(
            state,
            lifecycle="closed",
            model_wait_revision=None,
            model_wait_reason=None,
            batches=(),
            tools=(),
            terminal_waits=(),
            approvals=(),
            approval_reviews=(),
            retries=(),
        )
    raise TypeError(f"unsupported output activity event: {type(event).__name__}")


def _require_scope(
    context: OutputSurfaceContext,
    event: OutputActivityEvent,
) -> None:
    """拒绝不属于当前 OutputSession 的展示事件。"""
    if event.surface_id != context.surface_id or event.turn_id != context.turn_id:
        raise ValueError("output activity event does not match surface scope")


def _assistant_activity(
    identity: ResponseIdentity,
    item_id: str,
) -> AssistantActivity:
    """构建已经由端口校验的 assistant 展示身份。"""
    return AssistantActivity(identity, item_id)


def _assistant_is_superseded(
    state: TurnSurfaceState,
    item: AssistantActivity,
) -> bool:
    """判断一项正文是否属于已经失效的展示代次或 provider Attempt。"""
    identity = item.identity
    if _presentation_epoch_is_superseded(state, identity.presentation_epoch):
        return True
    return any(
        boundary.presentation_epoch == identity.presentation_epoch
        and boundary.round == identity.round
        and identity.attempt < boundary.attempt
        for boundary in state.provider_retry_boundaries
    )


def _presentation_epoch_is_superseded(
    state: TurnSurfaceState,
    presentation_epoch: int,
) -> bool:
    """判断展示代次是否已经被一项确定替换事实关闭。"""
    return any(
        replacement.superseded_epoch == presentation_epoch
        for replacement in state.presentation_replacements
    )


def _clear_superseded_content(state: TurnSurfaceState) -> TurnSurfaceState:
    """在替换事实与正文状态的同一次归约中释放旧正文所有权。"""
    current = state.visible_item
    if current is None and state.content == "buffered":
        current = next(
            (
                item
                for item in reversed(state.buffered_items)
                if item not in state.settled_items
            ),
            None,
        )
    if current is None or not _assistant_is_superseded(state, current):
        return state
    return replace(
        state,
        content="none",
        visible_item=None,
    )


def _retry_order(retry: RetryActivity) -> tuple[int, int, int]:
    """返回同一重试来源用于拒绝陈旧开始事件的稳定顺序。"""
    return (retry.presentation_epoch, retry.round, retry.attempt)


def _tool_by_id(
    tools: tuple[ToolActivity, ...],
    tool_id: str,
) -> ToolActivity | None:
    """读取一个活动工具身份。"""
    return next((item for item in tools if item.tool_id == tool_id), None)


def _terminal_wait_by_identity(
    waits: tuple[TerminalWaitActivity, ...],
    call_id: str,
    session_id: str,
) -> TerminalWaitActivity | None:
    """读取一个后台终端等待身份。"""
    return next((
        item
        for item in waits
        if item.call_id == call_id and item.session_id == session_id
    ), None)


def _approval_review_activity(
    event: ApprovalReviewStarted | ApprovalReviewCompleted,
) -> ApprovalReviewActivity:
    """从已校验的端口事实构建评审活动。"""
    return ApprovalReviewActivity(
        event.review_id,
        event.approval_id,
        event.call_id,
        event.action_summary,
        event.presentation_epoch,
    )


def _approval_review_by_id(
    reviews: tuple[ApprovalReviewActivity, ...],
    review_id: str,
) -> ApprovalReviewActivity | None:
    """按独立评审身份读取活动记录。"""
    return next(
        (item for item in reviews if item.review_id == review_id),
        None,
    )


if __name__ == '__main__':
    pass
