# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
from collections.abc import (
    Awaitable,
    Callable,
    Mapping,
)

from agent.application.turns.run_result import RunResult
from agent.application.views.builders.review import (
    build_review_cancelled_view,
    build_review_completed_view,
    build_review_failed_view,
    build_review_reconciliation_view,
    build_review_started_view,
    review_output_text,
)
from agent.ports import (
    ModelCapabilityError,
    ModelEventStream,
    ReviewCapability,
    ReviewObservationCapability,
)
from agent.ports.presentation import ApplicationSink
from agent.protocol import (
    CanonicalItem,
    ModelStreamEndReason,
    ReviewStreamRequest,
    SubmitReviewCommand,
)
from agent.protocol.json_value import (
    JsonValue as FrozenJsonValue,
    ThawedJsonValue,
)
from protocol.schema.json_value import JsonValue
from protocol.schema.identifiers import new_request_id
from protocol.schema.review import (
    ClientReviewWorkspace,
    MindReviewRequest,
    ReviewBaseBranchTarget,
    ReviewCommitTarget,
    ReviewCustomTarget,
    ReviewExecutionOptions,
    ReviewTarget,
    ReviewUncommittedTarget,
    parse_review_target,
    parse_review_output,
)
from protocol.schema.stream_events import (
    ReviewCancelledEvent,
    ReviewCompletedEvent,
    ReviewFailedEvent,
    ReviewReconciliationRequiredEvent,
    ReviewStartedEvent,
    StreamEvent,
    TurnCompletedEvent,
)
from protocol.schema.turn_inputs import TurnInput

ReviewEventSink = Callable[[StreamEvent], TurnInput | None]
ReviewStreamEndSink = Callable[[ModelStreamEndReason], None]
ReviewStreamOpener = Callable[[], Awaitable[ModelEventStream]]


def create_review_command(
    *,
    local_session_id: str,
    cid: str,
    sid: str,
    turn_id: str,
    target: ReviewTarget,
    workspace: ClientReviewWorkspace,
    llm_conf: Mapping[str, JsonValue],
    environment_snapshot: Mapping[str, FrozenJsonValue] | None,
) -> SubmitReviewCommand:
    """从类型化本地输入创建完整冻结且可持久化的 Review 命令。"""
    request = MindReviewRequest(
        request_id=new_request_id("review"),
        cid=cid,
        sid=sid,
        turn_id=turn_id,
        target=target,
        workspace=workspace,
        execution=ReviewExecutionOptions(
            llm_conf=dict(llm_conf),
            metadata={"cid": cid, "sid": sid},
        ),
    )
    return SubmitReviewCommand.create(
        session_id=local_session_id,
        request=ReviewStreamRequest.from_dict(request.request_payload()),
        environment_snapshot=environment_snapshot,
        trace_context={
            "remote_turn": {
                "cid": cid,
                "sid": sid,
                "turn_id": turn_id,
            },
        },
    )


async def run_review_turn(
    request: ReviewStreamRequest,
    environment_snapshot: dict[str, ThawedJsonValue] | None,
    *,
    capability: ReviewCapability,
    application: ApplicationSink,
    hint: str,
    on_event: ReviewEventSink | None = None,
    on_stream_end: ReviewStreamEndSink | None = None,
) -> RunResult:
    """登记一次可靠 Review 并从服务端确认的事件流投影结果。"""
    _ = environment_snapshot

    async def open_stream() -> ModelEventStream:
        """登记冻结请求并返回已确认身份的 Review 事件流。"""
        return await capability.review(request)

    return await _run_review_stream(
        open_stream,
        application=application,
        hint=hint,
        on_event=on_event,
        on_stream_end=on_stream_end,
    )


async def run_observed_review_turn(
    request: ReviewStreamRequest,
    *,
    capability: ReviewObservationCapability,
    application: ApplicationSink,
    hint: str,
    replay_target_seq: int,
    on_event: ReviewEventSink | None = None,
    on_stream_end: ReviewStreamEndSink | None = None,
) -> RunResult:
    """只 attach/replay 已登记 Review，并复用主动路径的事件投影。"""

    async def open_stream() -> ModelEventStream:
        """以冻结请求语义打开既有 Review 的重放流。"""
        return capability.observe_review(
            request,
            after_event_seq=0,
            replay_target_seq=replay_target_seq,
        )

    return await _run_review_stream(
        open_stream,
        application=application,
        hint=hint,
        on_event=on_event,
        on_stream_end=on_stream_end,
    )


async def _run_review_stream(
    open_stream: ReviewStreamOpener,
    *,
    application: ApplicationSink,
    hint: str,
    on_event: ReviewEventSink | None,
    on_stream_end: ReviewStreamEndSink | None,
) -> RunResult:
    """消费一种 Review 事件流，并只从 Canonical Item 生成可见结果。"""
    event_count = 0
    terminal_status = ""
    review_text = ""
    review_error = ""
    reconciliation_visible = False
    stream = None
    stream_end_reason: ModelStreamEndReason = "fatal"
    try:
        stream = await open_stream()
        async for event in stream:
            event_count += 1
            if on_event is not None:
                on_event(event)
            current_item = stream.current_item
            if isinstance(event, ReviewStartedEvent):
                _require_review_item(event, current_item)
                application.emit(build_review_started_view(hint))
                continue
            if isinstance(event, ReviewCompletedEvent):
                item = _require_review_item(event, current_item)
                payload = item.payload_value().get("output")
                output = parse_review_output(payload)
                review_text = review_output_text(output)
                application.emit(build_review_completed_view(output))
                continue
            if isinstance(event, ReviewFailedEvent):
                _require_review_item(event, current_item)
                review_error = event.error
                application.emit(build_review_failed_view(event.error))
                continue
            if isinstance(event, ReviewCancelledEvent):
                _require_review_item(event, current_item)
                application.emit(build_review_cancelled_view(event.reason))
                continue
            if isinstance(event, ReviewReconciliationRequiredEvent):
                _require_review_item(event, current_item)
                reconciliation_visible = True
                application.emit(build_review_reconciliation_view(
                    event.error,
                    effect_id=event.effect_id,
                ))
                continue
            if isinstance(event, TurnCompletedEvent):
                terminal_status = event.status

        if not terminal_status:
            message = "Review observation ended before turn.completed."
            if not reconciliation_visible:
                application.emit(build_review_reconciliation_view(message))
            stream_end_reason = "protocol_error"
            return RunResult(
                status="reconciliation_required",
                error=message,
                error_code="review_observation_incomplete",
            )
        stream_end_reason = "settled"
        if terminal_status == "completed":
            return RunResult(
                status="completed",
                assistant_text=review_text,
            )
        if terminal_status in {"interrupted", "cancelled"}:
            return RunResult(status=terminal_status)
        return RunResult(
            status="failed",
            error=review_error or "Review failed.",
        )
    except asyncio.CancelledError:
        stream_end_reason = "cancelled"
        raise
    except ModelCapabilityError as error:
        if event_count:
            stream_end_reason = "protocol_error"
        uncertain = bool(
            error.retryable
            or error.details.get("submission_unknown") is True
            or event_count
        )
        if uncertain:
            if not reconciliation_visible:
                application.emit(build_review_reconciliation_view(
                    error.message,
                ))
            return RunResult(
                status="reconciliation_required",
                error=error.message,
                error_code=error.code,
                error_details=error.details,
            )
        application.emit(build_review_failed_view(error.message))
        return RunResult(
            status="failed",
            error=error.message,
            error_code=error.code,
            error_details=error.details,
        )
    except Exception as error:
        message = str(error).strip() or type(error).__name__
        if event_count:
            stream_end_reason = "protocol_error"
            if not reconciliation_visible:
                application.emit(build_review_reconciliation_view(message))
            return RunResult(status="reconciliation_required", error=message)
        application.emit(build_review_failed_view(message))
        return RunResult(status="failed", error=message)
    finally:
        if stream is not None:
            await stream.aclose()
        if on_stream_end is not None:
            on_stream_end(stream_end_reason)


def review_target_hint(target: ReviewTarget) -> str:
    """返回提交准备和 Review 启动态共享的目标摘要。"""
    if isinstance(target, ReviewUncommittedTarget):
        return "current changes"
    if isinstance(target, ReviewBaseBranchTarget):
        return f"changes against '{target.branch}'"
    if isinstance(target, ReviewCommitTarget):
        title = f": {target.title}" if target.title else ""
        return f"commit {target.sha[:7]}{title}"
    if isinstance(target, ReviewCustomTarget):
        return target.instructions
    raise TypeError("unsupported Review target")


def review_request_hint(request: ReviewStreamRequest) -> str:
    """从已校验的冻结请求恢复 Review 目标摘要。"""
    if not isinstance(request, ReviewStreamRequest):
        raise TypeError("review request is required")
    target = request.to_dict()["target"]
    return review_target_hint(parse_review_target(target))


def _require_review_item(
    event: StreamEvent,
    item: CanonicalItem | None,
) -> CanonicalItem:
    """返回与当前 Review 事件一致的 Canonical Item。"""
    if (
        item is None
        or item.item_kind != "review"
        or item.item_id != event.item_id
        or item.item_status != event.item_status
    ):
        raise ValueError("canonical Review projection does not match event")
    return item


if __name__ == '__main__':
    pass
