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
    TranscriptSink,
    TurnObservationCapability,
)
from agent.protocol import TurnObservationRequest


@typing.runtime_checkable
class TurnStreamSource(typing.Protocol):
    """定义共享 Turn 生命周期获取事件流的单一来源。

    实现方必须明确区分新提交和既有 Turn 观察；观察源不得调用模型提交入口，
    但必须保留 Stop continuation 创建新 Turn 所需的提交能力。
    """

    @property
    def continuation_capability(self) -> ModelCapability:
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


class SubmittingTurnStreamSource:
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


class ObservingTurnStreamSource:
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
        """跳过已经在 Queue add 前执行过的提交 Hook。"""
        del hook_events, transcript, options
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
        del pref_config, message, tools
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


if __name__ == '__main__':
    pass
