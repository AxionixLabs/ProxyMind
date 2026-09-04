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

    async def prepare(
        self,
        hook_events: TurnHookEvents,
        transcript: TranscriptSink,
        message: str,
        options: dict[str, typing.Any],
    ) -> str:
        """在事件流创建前完成来源拥有的提交准备。"""
        ...

    def open(
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

    def __init__(self, capability: ModelCapability) -> None:
        if not isinstance(capability, ModelCapability):
            raise RuntimeError("model capability is required")
        self._capability = capability

    @property
    def continuation_capability(self) -> ModelCapability:
        """返回当前新 Turn 提交能力。"""
        return self._capability

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

    def open(
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
        return self._capability.stream(
            request,
            on_recovery_status=on_recovery_status,
            on_approval_snapshot=on_approval_snapshot,
        )


class ObservingTurnStreamSource:
    """只 attach 已由 Queue 命令创建的远端 Turn。"""

    def __init__(
        self,
        observer: TurnObservationCapability,
        continuation_capability: ModelCapability,
    ) -> None:
        if not isinstance(observer, TurnObservationCapability):
            raise RuntimeError("turn observer is required")
        if not isinstance(continuation_capability, ModelCapability):
            raise RuntimeError("model capability is required")
        self._observer = observer
        self._continuation_capability = continuation_capability

    @property
    def continuation_capability(self) -> ModelCapability:
        """返回观察结束后 Stop continuation 使用的提交能力。"""
        return self._continuation_capability

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

    def open(
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
            ),
            on_recovery_status=on_recovery_status,
            on_approval_snapshot=on_approval_snapshot,
        )


if __name__ == '__main__':
    pass
