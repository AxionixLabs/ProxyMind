# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from agent.adapters.protocol.model_request import (
    build_model_stream_request,
    extend_request_context,
)
from agent.application.turns.context import TurnContext
from agent.harness.hooks.turn_lifecycle import TurnHookEvents
from agent.ports import (
    ApprovalSnapshotCallback,
    ModelCapability,
    ModelEventStream,
    ModelRequestFrozenCallback,
    RecoveryStatusCallback,
    ReviewCapability,
    ReviewObservationCapability,
    TranscriptSink,
    TurnObservationCapability,
)
from agent.protocol import (
    ReviewStreamRequest,
    TurnObservationRequest,
)


@typing.runtime_checkable
class TurnStreamSource(typing.Protocol):
    """定义共享 Turn 生命周期获取事件流的单一来源。

    实现方必须明确区分新提交和既有 Turn 观察；观察源不得调用模型提交入口，
    但必须保留 Stop continuation 创建新 Turn 所需的提交能力。
    共享生命周期按统一签名调用，实现方仅使用其职责所需的参数；观察与 Review
    来源不得用当前提交内容重建已有或冻结的请求。
    """

    @property
    def continuation_capability(self) -> ModelCapability | None:
        """返回 Stop continuation 创建新 Turn 使用的模型能力。"""
        ...

    @property
    def records_local_start(self) -> bool:
        """返回共享流是否应写入新的本地 Turn 起始记录。"""
        ...

    @property
    def initial_wait_visible(self) -> bool:
        """返回事件读取前是否应显示新 Turn 的模型等待。"""
        ...

    @property
    def historical_replay_target_seq(self) -> int | None:
        """返回只允许归约、不得直接执行副作用的历史水位。"""
        ...

    async def prepare(
        self,
        hook_events: TurnHookEvents,
        transcript: TranscriptSink,
        message: str,
        options: dict[str, typing.Any],
    ) -> str:
        """在事件流创建前完成来源拥有的提交准备。"""
        ...

    async def open(
        self,
        context: TurnContext,
        *,
        pref_config: dict[str, typing.Any],
        message: str,
        tools: list[dict[str, typing.Any]],
        options: dict[str, typing.Any],
        on_recovery_status: RecoveryStatusCallback,
        on_approval_snapshot: ApprovalSnapshotCallback,
    ) -> ModelEventStream:
        """创建可关闭的模型事件流。"""
        ...


class SubmittingTurnStreamSource(TurnStreamSource):
    """运行提交 Hook 并为一条新 Turn 创建模型事件流。"""

    def __init__(
        self,
        capability: ModelCapability,
        request_frozen: ModelRequestFrozenCallback | None = None,
    ) -> None:
        if not isinstance(capability, ModelCapability):
            raise RuntimeError("model capability is required")
        if request_frozen is not None and not callable(request_frozen):
            raise TypeError("model request frozen callback must be callable")
        self._capability = capability
        self._request_frozen = request_frozen

    @property
    def continuation_capability(self) -> ModelCapability:
        """返回当前新 Turn 提交能力。"""
        return self._capability

    @property
    def records_local_start(self) -> bool:
        """新提交 Turn 必须写入本地起始记录。"""
        return True

    @property
    def initial_wait_visible(self) -> bool:
        """新提交 Turn 在首个事件前显示模型等待。"""
        return True

    @property
    def historical_replay_target_seq(self) -> int | None:
        """新提交 Turn 不包含历史重放前缀。"""
        return None

    async def prepare(
        self,
        hook_events: TurnHookEvents,
        transcript: TranscriptSink,
        message: str,
        options: dict[str, typing.Any],
    ) -> str:
        """运行一次 SessionStart/UserPromptSubmit 并合并 Hook 上下文。"""
        result = await hook_events.begin(message)
        if result.message != message:
            transcript.append(
                "message.updated",
                actor="user",
                payload={"content": result.message, "source": "hook"},
            )
        extend_request_context(
            options,
            additional_context=result.additional_context,
        )
        return result.message

    async def open(
        self,
        context: TurnContext,
        *,
        pref_config: dict[str, typing.Any],
        message: str,
        tools: list[dict[str, typing.Any]],
        options: dict[str, typing.Any],
        on_recovery_status: RecoveryStatusCallback,
        on_approval_snapshot: ApprovalSnapshotCallback,
    ) -> ModelEventStream:
        """从完整冻结请求创建新模型流。"""
        request = build_model_stream_request(
            context,
            pref_config=pref_config,
            message=message,
            tools=tools,
            options=options,
        )
        if self._request_frozen is not None:
            await self._request_frozen(request)
        return self._capability.stream(
            request,
            on_recovery_status=on_recovery_status,
            on_approval_snapshot=on_approval_snapshot,
        )


class ObservingTurnStreamSource(TurnStreamSource):
    """只 attach 已由其他提交路径创建的既有远端 Turn。"""

    def __init__(
        self,
        observer: TurnObservationCapability,
        continuation_capability: ModelCapability,
        *,
        after_event_seq: int | None = None,
        replay_target_seq: int | None = None,
        records_local_start: bool = True,
    ) -> None:
        if not isinstance(observer, TurnObservationCapability):
            raise RuntimeError("turn observer is required")
        if not isinstance(continuation_capability, ModelCapability):
            raise RuntimeError("model capability is required")
        for field_name, value in (
            ("after_event_seq", after_event_seq),
            ("replay_target_seq", replay_target_seq),
        ):
            if value is not None and (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
            ):
                raise ValueError(f"{field_name} must be non-negative")
        if (
            after_event_seq is not None
            and replay_target_seq is not None
            and replay_target_seq < after_event_seq
        ):
            raise ValueError("replay target precedes observation cursor")
        if not isinstance(records_local_start, bool):
            raise TypeError("records_local_start must be boolean")
        self._observer = observer
        self._continuation_capability = continuation_capability
        self._after_event_seq = after_event_seq
        self._replay_target_seq = replay_target_seq
        self._records_local_start = records_local_start

    @property
    def continuation_capability(self) -> ModelCapability:
        """返回观察结束后 Stop continuation 使用的提交能力。"""
        return self._continuation_capability

    @property
    def records_local_start(self) -> bool:
        """返回该 observer 是否代表尚未写入本地记录的新 Turn。"""
        return self._records_local_start

    @property
    def initial_wait_visible(self) -> bool:
        """已有明确 replay 目标时从首帧起静默归约历史事件。"""
        replay_target = self._replay_target_seq
        replay_start = self._after_event_seq or 0
        return replay_target is None or replay_target <= replay_start

    @property
    def historical_replay_target_seq(self) -> int | None:
        """返回 attach 建立时冻结的权威历史事件水位。"""
        return self._replay_target_seq

    async def prepare(
        self,
        hook_events: TurnHookEvents,
        transcript: TranscriptSink,
        message: str,
        options: dict[str, typing.Any],
    ) -> str:
        """观察已有 Turn 时跳过已经执行过的提交 Hook。"""
        return message

    async def open(
        self,
        context: TurnContext,
        *,
        pref_config: dict[str, typing.Any],
        message: str,
        tools: list[dict[str, typing.Any]],
        options: dict[str, typing.Any],
        on_recovery_status: RecoveryStatusCallback,
        on_approval_snapshot: ApprovalSnapshotCallback,
    ) -> ModelEventStream:
        """只使用服务端确认坐标建立 attach/replay 观察流。"""
        timeout = options.pop("timeout", 60.0)
        return self._observer.observe(
            TurnObservationRequest(
                cid=context.cid,
                sid=context.sid,
                turn_id=context.turn_id,
                timeout=timeout,
                after_event_seq=self._after_event_seq,
                replay_target_seq=self._replay_target_seq,
            ),
            on_recovery_status=on_recovery_status,
            on_approval_snapshot=on_approval_snapshot,
        )


class SubmittingReviewTurnStreamSource(TurnStreamSource):
    """登记冻结 Review 请求并把确认后的事件流交给共享 Turn 生命周期。"""

    def __init__(
        self,
        capability: ReviewCapability,
        request: ReviewStreamRequest,
    ) -> None:
        """绑定 Review 登记能力和不可变请求。"""
        if not isinstance(capability, ReviewCapability):
            raise RuntimeError("review capability is required")
        if not isinstance(request, ReviewStreamRequest):
            raise TypeError("review stream request is required")
        self._capability = capability
        self._request = request

    @property
    def continuation_capability(self) -> ModelCapability | None:
        """Review 终态不允许 Stop Hook 创建普通模型续轮。"""
        return None

    @property
    def records_local_start(self) -> bool:
        """新登记 Review 必须写入本地 Turn 起始记录。"""
        return True

    @property
    def initial_wait_visible(self) -> bool:
        """Review 登记和首个事件等待期间显示模型等待。"""
        return True

    @property
    def historical_replay_target_seq(self) -> int | None:
        """新登记 Review 不包含历史重放前缀。"""
        return None

    async def prepare(
        self,
        hook_events: TurnHookEvents,
        transcript: TranscriptSink,
        message: str,
        options: dict[str, typing.Any],
    ) -> str:
        """Review 请求已冻结，不运行普通 UserPromptSubmit Hook。"""
        return message

    async def open(
        self,
        context: TurnContext,
        *,
        pref_config: dict[str, typing.Any],
        message: str,
        tools: list[dict[str, typing.Any]],
        options: dict[str, typing.Any],
        on_recovery_status: RecoveryStatusCallback,
        on_approval_snapshot: ApprovalSnapshotCallback,
    ) -> ModelEventStream:
        """登记同一坐标的 Review 并返回服务端确认后的事件流。"""
        _require_review_context(context, self._request)
        return await self._capability.review(
            self._request,
            on_recovery_status=on_recovery_status,
            on_approval_snapshot=on_approval_snapshot,
        )


class ObservingReviewTurnStreamSource(TurnStreamSource):
    """只 attach 已登记 Review，并保留共享事件泵的历史水位语义。"""

    def __init__(
        self,
        capability: ReviewObservationCapability,
        request: ReviewStreamRequest,
        *,
        after_event_seq: int = 0,
        replay_target_seq: int,
        records_local_start: bool = True,
    ) -> None:
        """绑定冻结请求、观察能力和恢复时的权威事件水位。"""
        if not isinstance(capability, ReviewObservationCapability):
            raise RuntimeError("review observation capability is required")
        if not isinstance(request, ReviewStreamRequest):
            raise TypeError("review stream request is required")
        for field_name, value in (
            ("after_event_seq", after_event_seq),
            ("replay_target_seq", replay_target_seq),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
            ):
                raise ValueError(f"{field_name} must be non-negative")
        if replay_target_seq < after_event_seq:
            raise ValueError("replay target precedes observation cursor")
        if not isinstance(records_local_start, bool):
            raise TypeError("records_local_start must be boolean")
        self._capability = capability
        self._request = request
        self._after_event_seq = after_event_seq
        self._replay_target_seq = replay_target_seq
        self._records_local_start = records_local_start

    @property
    def continuation_capability(self) -> ModelCapability | None:
        """Review attach 完成后不允许创建普通模型续轮。"""
        return None

    @property
    def records_local_start(self) -> bool:
        """返回本次恢复是否还需补写本地 Turn 起始记录。"""
        return self._records_local_start

    @property
    def initial_wait_visible(self) -> bool:
        """历史重放阶段静默归约，追平后才展示等待。"""
        return self._replay_target_seq <= self._after_event_seq

    @property
    def historical_replay_target_seq(self) -> int | None:
        """返回 attach 建立时冻结的权威历史事件水位。"""
        return self._replay_target_seq

    async def prepare(
        self,
        hook_events: TurnHookEvents,
        transcript: TranscriptSink,
        message: str,
        options: dict[str, typing.Any],
    ) -> str:
        """恢复路径不重复运行普通提交 Hook。"""
        return message

    async def open(
        self,
        context: TurnContext,
        *,
        pref_config: dict[str, typing.Any],
        message: str,
        tools: list[dict[str, typing.Any]],
        options: dict[str, typing.Any],
        on_recovery_status: RecoveryStatusCallback,
        on_approval_snapshot: ApprovalSnapshotCallback,
    ) -> ModelEventStream:
        """只按冻结身份和水位打开 Review attach/replay 流。"""
        _require_review_context(context, self._request)
        return self._capability.observe_review(
            self._request,
            after_event_seq=self._after_event_seq,
            replay_target_seq=self._replay_target_seq,
            on_recovery_status=on_recovery_status,
        )


def _require_review_context(
    context: TurnContext,
    request: ReviewStreamRequest,
) -> None:
    """确认共享 Turn 上下文没有改变冻结 Review 的远端身份。"""
    if (
        context.cid != request.cid
        or context.sid != request.sid
        or context.turn_id != request.turn_id
    ):
        raise ValueError("review request does not match turn context")


if __name__ == '__main__':
    pass
