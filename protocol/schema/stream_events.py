# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import copy
import math
import re
import typing
from collections.abc import Mapping
from dataclasses import (
    dataclass,
    field,
)

from protocol.schema.context_usage import (
    ContextUsageSnapshot,
    parse_context_usage,
)
from protocol.schema.item_projection import (
    ItemKind,
    ItemStatus,
    builtin_done_item_status as _builtin_done_item_status,
    default_item_projection as _default_item_projection,
    reject_item_projection as _reject_item_projection,
    tool_output_item_status as _tool_output_item_status,
    validate_item_fields as _item_fields,
)
from protocol.schema.json_value import JsonValue
from protocol.schema.review import (
    ReviewOutput,
    ReviewTarget,
    parse_review_output,
    parse_review_target,
)
from protocol.schema.tool_approval import (
    TOOL_APPROVAL_DECISIONS,
    TOOL_APPROVAL_DECISIONS_BY_KIND,
    ToolApprovalDecision,
    ToolApprovalKind,
    ToolApprovalSnapshotStatus
)
from protocol.schema.turn_lifecycle import (
    TurnCompletedStatus,
    parse_turn_completed_status,
)

EffectReplay: typing.TypeAlias = typing.Literal[
    "safe",
    "manual"
]

AssistantTextPhase: typing.TypeAlias = typing.Literal[
    "commentary",
    "final_answer",
]

ApprovalReviewStatus: typing.TypeAlias = typing.Literal[
    "in_progress",
    "approved",
    "denied",
    "timed_out",
    "aborted",
]

ApprovalReviewRiskLevel: typing.TypeAlias = typing.Literal[
    "low",
    "medium",
    "high",
    "critical",
]

ApprovalReviewUserAuthorization: typing.TypeAlias = typing.Literal[
    "unknown",
    "low",
    "medium",
    "high",
]

ApprovalReviewDecisionSource: typing.TypeAlias = typing.Literal["agent"]

ContextCompactionPhase: typing.TypeAlias = typing.Literal[
    "pre_turn",
    "mid_turn",
    "standalone",
]

ContextCompactionTrigger: typing.TypeAlias = typing.Literal[
    "automatic",
    "manual",
]


@dataclass(frozen=True, slots=True)
class ExecutionEffect:
    """描述客户端执行前必须遵守的持久效果约束。"""
    effect_id: str
    fingerprint: str
    replay: EffectReplay


@dataclass(frozen=True, slots=True, kw_only=True)
class StreamEvent:
    """描述流式协议事件的公共字段。"""
    type: str
    proto: str = ""
    cid: str = ""
    sid: str = ""
    turn_id: str = ""
    event_seq: int | None = None
    round: int | None = None
    presentation_epoch: int = 1
    display: dict[str, typing.Any] | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ItemStreamEvent(StreamEvent):
    """描述带有稳定 Canonical Item 投影的可展示事件。"""
    item_id: str = ""
    item_kind: ItemKind = "custom"
    item_status: ItemStatus = "registered"


@dataclass(frozen=True, slots=True, kw_only=True)
class MarkerEvent(StreamEvent):
    """描述不携带业务载荷的流式标记。"""


@dataclass(frozen=True, slots=True, kw_only=True)
class ContextUsageUpdatedEvent(StreamEvent):
    """交付会话上下文快照，不创建 Item 或改变 Turn 生命周期。"""

    snapshot: ContextUsageSnapshot


@dataclass(frozen=True, slots=True, kw_only=True)
class StreamGapEvent(StreamEvent):
    """描述非持久事件回放缺口控制信号。"""
    gap_kind: typing.Literal["retained_prefix", "internal"]
    requested_after_seq: int
    first_event_seq: int | None = None
    next_seq: int | None = None
    expected_event_seq: int | None = None
    observed_event_seq: int | None = None
    replay_required: bool = False
    retryable: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class TurnTerminalEvent(StreamEvent):
    """描述模型轮次终态携带的响应元数据。"""
    response_id: str = ""
    model: str = ""
    route: str = ""
    request_id: str = ""
    service_tier: str = ""
    usage: dict[str, typing.Any] = field(default_factory=dict)
    stop_reason: str | None = None
    stop_sequence: str | None = None

    def __post_init__(self) -> None:
        """复制用量数据，避免外部引用修改不可变事件。"""
        object.__setattr__(self, "usage", copy.deepcopy(dict(self.usage or {})))


@dataclass(frozen=True, slots=True, kw_only=True)
class TurnCompletedEvent(TurnTerminalEvent):
    """描述已与 Durable Turn 同事务提交的唯一权威终态。"""
    status: TurnCompletedStatus
    last_event_seq: int
    completed_at: float
    duration_ms: int | None = None
    error: str = ""
    error_type: str = ""
    error_source: str = ""
    status_code: int | None = None
    retryable: bool | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class TurnRetryingEvent(StreamEvent):
    """描述当前 Turn 内一次可恢复的 provider 流重试。"""
    round: int
    attempt: int
    max_attempts: int
    retry_in_ms: int
    reason: str = "stream_error"
    supersedes_item_id: str = ""
    error_type: str = ""
    error_source: str = ""
    retryable: bool | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class TurnInputAcceptedEvent(StreamEvent):
    """描述已写入当前逻辑轮次的引导输入。"""
    client_message_id: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionTitleUpdatedEvent(StreamEvent):
    """描述服务端已提交的会话标题更新。"""
    title: str


@dataclass(frozen=True, slots=True, kw_only=True)
class TurnReconciliationRequiredEvent(StreamEvent):
    """描述持久效果结果不确定且轮次暂停结算的状态。"""
    status: typing.Literal["reconciliation_required"] = "reconciliation_required"
    effect_id: str = ""
    error: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class PresentationSupersededEvent(StreamEvent):
    """描述旧 Attempt 展示已经被新的 presentation epoch 取代。"""
    superseded_epoch: int = 0
    reason: str = "attempt_restarted"


@dataclass(frozen=True, slots=True, kw_only=True)
class ContextCompactionEvent(ItemStreamEvent):
    """描述服务端上下文压缩尝试的权威 Item 生命周期。"""
    phase: ContextCompactionPhase
    trigger: ContextCompactionTrigger
    reason: str
    error_type: str | None = None
    retryable: bool | None = None
    before_items: int | None = None
    after_items: int | None = None
    before_chars: int | None = None
    after_chars: int | None = None
    dropped_items: int | None = None
    reduction_ratio: float | None = None
    latency_ms: int | None = None
    replacement_version: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ReviewStartedEvent(ItemStreamEvent):
    """描述 Review Item 已开始执行。"""
    review_item_id: str
    status: typing.Literal["in_progress"]
    target: ReviewTarget
    workspace_revision: str
    prompt_version: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ReviewCompletedEvent(ItemStreamEvent):
    """描述 Review Item 已产生结构化结果。"""
    review_item_id: str
    status: typing.Literal["completed"]
    output: ReviewOutput


@dataclass(frozen=True, slots=True, kw_only=True)
class ReviewFailedEvent(ItemStreamEvent):
    """描述 Review Item 已失败。"""
    review_item_id: str
    status: typing.Literal["failed"]
    error: str
    output_preview: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class ReviewCancelledEvent(ItemStreamEvent):
    """描述 Review Item 已取消。"""
    review_item_id: str
    status: typing.Literal["cancelled"]
    reason: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ReviewReconciliationRequiredEvent(ItemStreamEvent):
    """描述 Review Item 因持久效果不确定而暂停。"""
    review_item_id: str
    status: typing.Literal["reconciliation_required"]
    effect_id: str
    error: str


@dataclass(frozen=True, slots=True, kw_only=True)
class TextDeltaEvent(ItemStreamEvent):
    """描述 assistant 正文增量。"""
    text: str = ""
    segment_id: str = ""
    phase: AssistantTextPhase | None = None

    def __post_init__(self) -> None:
        """补齐正文增量的稳定 Item 投影。"""
        object.__setattr__(self, "phase", _assistant_text_phase(self.phase))
        _default_item_projection(
            self,
            item_id=self.segment_id,
            item_kind="text",
            item_status="in_progress",
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class TextDoneEvent(ItemStreamEvent):
    """描述 assistant 正文段完成事件。"""
    segment_id: str = ""
    final_text: str | None = None
    phase: AssistantTextPhase | None = None

    def __post_init__(self) -> None:
        """补齐正文完成事件的稳定 Item 投影。"""
        object.__setattr__(self, "phase", _assistant_text_phase(self.phase))
        _default_item_projection(
            self,
            item_id=self.segment_id,
            item_kind="text",
            item_status="completed",
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class TextMetaEvent(ItemStreamEvent):
    """描述 assistant 正文段的来源与标注元数据。"""
    segment_id: str = ""
    phase: AssistantTextPhase | None = None
    annotations: tuple[typing.Any, ...] | None = None
    citations: tuple[typing.Any, ...] | None = None
    sources: tuple[typing.Any, ...] | None = None
    source_count: int | None = None
    builtin_call_ids: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        """补齐正文元数据事件的稳定 Item 投影。"""
        object.__setattr__(self, "phase", _assistant_text_phase(self.phase))
        _default_item_projection(
            self,
            item_id=self.segment_id,
            item_kind="text",
            item_status="completed",
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolBuiltinCallEvent(ItemStreamEvent):
    """描述 provider 内置工具执行过程。"""
    builtin_call_id: str = ""
    builtin_type: str = ""
    payload: dict[str, typing.Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """复制内置工具载荷。"""
        _default_item_projection(
            self,
            item_id=self.builtin_call_id,
            item_kind="builtin_tool",
            item_status="in_progress",
        )
        object.__setattr__(self, "payload", copy.deepcopy(dict(self.payload or {})))


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolBuiltinDoneEvent(ItemStreamEvent):
    """描述内置工具完成后的来源元数据。"""
    builtin_call_id: str = ""
    builtin_type: str = ""
    sources: tuple[typing.Any, ...] | None = None
    source_count: int | None = None

    def __post_init__(self) -> None:
        """补齐内置工具完成事件的稳定 Item 投影。"""
        _default_item_projection(
            self,
            item_id=self.builtin_call_id,
            item_kind="builtin_tool",
            item_status=_builtin_done_item_status(
                getattr(self, "status", "completed")
            ),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolEvent(ItemStreamEvent):
    """描述工具事件的公共调用字段。"""
    name: str = ""
    call_id: str = ""
    arguments: dict[str, typing.Any] = field(default_factory=dict)
    reason: str = ""

    def __post_init__(self) -> None:
        """复制工具事件中的可变映射字段。"""
        if self.type == "tool.call":
            item_id = self.call_id
            item_kind: ItemKind = "tool_call"
            item_status: ItemStatus = "waiting_result"
        else:
            item_id = f"{self.call_id}:output" if self.call_id else ""
            item_kind = "tool_output"
            payload = getattr(self, "payload", {})
            status = payload.get("status") if isinstance(payload, dict) else None
            item_status = _tool_output_item_status(status)
        _default_item_projection(
            self,
            item_id=item_id,
            item_kind=item_kind,
            item_status=item_status,
        )
        object.__setattr__(
            self,
            "arguments",
            copy.deepcopy(dict(self.arguments or {})),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolApprovalRequiredEvent(ItemStreamEvent):
    """描述需要客户端决定的直接工具审批请求。"""
    call_id: str = ""
    reason: str = ""
    kind: ToolApprovalKind = "command"
    approval_id: str = ""
    status: ToolApprovalSnapshotStatus = "pending"
    ack: dict[str, typing.Any] | None = None
    environment_id: str | None = None
    started_at_ms: int = 0
    plugin_id: str | None = None
    script_path: str | None = None
    tty: bool = False
    sandbox_permissions: str = "use_default"
    additional_permissions: dict[str, typing.Any] | None = None
    policy_fingerprint: str | None = None
    patch_scope: tuple[str, ...] = ()
    files: tuple[str, ...] = ()
    permissions_preapproved: bool | None = None
    command: typing.Any = ""
    patch: typing.Any = ""
    cwd: str | None = None
    cwd_raw: str | None = None
    proposed_execpolicy_amendment: dict[str, typing.Any] | None = None
    proposed_network_policy_amendment: dict[str, typing.Any] | None = None
    session_id: str = ""
    input: str = ""
    control: typing.Literal["none", "interrupt", "terminate"] = "none"
    target: str = ""
    host: str = ""
    protocol: str = ""
    port: int | None = None
    permissions: dict[str, typing.Any] | None = None
    scope: str | None = None
    strict_auto_review: bool | None = None
    arguments: typing.Any = None
    server: str = ""
    tool_name: str = ""
    mcp_request_id: str = ""
    connector_id: str | None = None
    connector_name: str | None = None
    connector_description: str | None = None
    connected_account_email: str | None = None
    tool_title: str | None = None
    tool_description: str | None = None
    annotations: dict[str, bool | None] | None = None
    available_decisions: tuple[ToolApprovalDecision, ...] = ()
    parsed_cmd: tuple[typing.Any, ...] = ()

    def __post_init__(self) -> None:
        """补齐审批事件的稳定 Item 投影。"""
        _default_item_projection(
            self,
            item_id=self.approval_id,
            item_kind="approval",
            item_status="waiting_approval",
        )


@dataclass(frozen=True, slots=True)
class ApprovalReview:
    """描述自动审批 reviewer 的结构化状态与解释。"""
    status: ApprovalReviewStatus
    risk_level: ApprovalReviewRiskLevel | None = None
    user_authorization: ApprovalReviewUserAuthorization | None = None
    rationale: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolApprovalReviewEvent(ItemStreamEvent):
    """描述与本地审批事实分离的自动评审生命周期。"""
    review_id: str = ""
    approval_id: str = ""
    call_id: str = ""
    target_item_id: str = ""
    kind: ToolApprovalKind = "command"
    action: dict[str, typing.Any] = field(default_factory=dict)
    review: ApprovalReview = field(
        default_factory=lambda: ApprovalReview(status="in_progress")
    )
    started_at_ms: int = 0

    def __post_init__(self) -> None:
        """补齐评审 Item 投影并隔离外部动作载荷。"""
        _default_item_projection(
            self,
            item_id=self.review_id,
            item_kind="approval",
            item_status=_approval_review_item_status(self.review.status),
        )
        object.__setattr__(self, "action", copy.deepcopy(dict(self.action)))


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolApprovalReviewStartedEvent(ToolApprovalReviewEvent):
    """描述自动审批评审已经开始。"""


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolApprovalReviewCompletedEvent(ToolApprovalReviewEvent):
    """描述自动审批评审已经形成确定终态。"""
    completed_at_ms: int = 0
    decision_source: ApprovalReviewDecisionSource = "agent"


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolCallEvent(ToolEvent):
    """描述服务端下发的原始客户端工具调用。"""


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolCallsStartEvent(StreamEvent):
    """描述客户端工具批次已原子登记并可以开始接收调用。"""
    batch_id: str = ""
    call_ids: tuple[str, ...] = ()
    count: int = 0
    ready: typing.Literal[True] = True
    timeout_sec: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolCallsDoneEvent(StreamEvent):
    """描述客户端工具批次的调用事件已经完整发送。"""
    batch_id: str = ""
    call_ids: tuple[str, ...] = ()
    count: int = 0


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolOutputEvent(ToolEvent):
    """描述服务端工具输出回灌事件。"""
    payload: dict[str, typing.Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """复制工具输出载荷。"""
        ToolEvent.__post_init__(self)
        object.__setattr__(self, "payload", copy.deepcopy(dict(self.payload or {})))


@dataclass(frozen=True, slots=True, kw_only=True)
class UnknownStreamEvent(StreamEvent):
    """描述尚未建模的扩展事件。"""
    payload: dict[str, typing.Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """复制未建模事件的原始载荷。"""
        object.__setattr__(self, "payload", copy.deepcopy(dict(self.payload or {})))


ChatStreamEvent: typing.TypeAlias = (
    MarkerEvent
    | StreamGapEvent
    | TurnCompletedEvent
    | TurnRetryingEvent
    | TurnInputAcceptedEvent
    | SessionTitleUpdatedEvent
    | TurnReconciliationRequiredEvent
    | PresentationSupersededEvent
    | ContextCompactionEvent
    | ContextUsageUpdatedEvent
    | ReviewStartedEvent
    | ReviewCompletedEvent
    | ReviewFailedEvent
    | ReviewCancelledEvent
    | ReviewReconciliationRequiredEvent
    | TextDeltaEvent
    | TextDoneEvent
    | TextMetaEvent
    | ToolBuiltinCallEvent
    | ToolBuiltinDoneEvent
    | ToolApprovalRequiredEvent
    | ToolApprovalReviewStartedEvent
    | ToolApprovalReviewCompletedEvent
    | ToolCallEvent
    | ToolCallsStartEvent
    | ToolCallsDoneEvent
    | ToolOutputEvent
    | UnknownStreamEvent
)

_MARKER_EVENT_TYPES = {
    "ping",
    "turn.started",
    "turn.thinking",
}

_REMOVED_EVENT_TYPES = frozenset({
    "turn.start",
    "turn.done",
    "turn.failed",
    "turn.logical_settled",
})

_ITEM_EVENT_TYPES = frozenset({
    "context.compaction.started",
    "context.compaction.completed",
    "context.compaction.failed",
    "review.started",
    "review.completed",
    "review.failed",
    "review.cancelled",
    "review.reconciliation_required",
    "text.delta",
    "text.done",
    "text.meta",
    "tool.call",
    "tool.output",
    "tool.approval_required",
    "tool.approval_review.started",
    "tool.approval_review.completed",
    "tool.builtin.call",
    "tool.builtin.done",
})


def parse_stream_event(
    payload: Mapping[str, typing.Any]
) -> ChatStreamEvent:
    """把流式协议对象解析为稳定事件类型。"""
    if not isinstance(payload, Mapping):
        raise TypeError("stream event must be an object")

    raw = dict(payload)

    event_type = str(raw.get("type") or "").strip()
    if not event_type:
        raise ValueError("stream event type is required")
    if event_type in _REMOVED_EVENT_TYPES:
        raise ValueError(f"stream event type was removed: {event_type}")

    if event_type != "ping" and (
        "seq" in raw or "replace_current_response" in raw
    ):
        raise ValueError("stream event contains a removed protocol field")

    if event_type == "stream.gap":
        return _stream_gap_event(raw)

    common = _common_fields(raw, event_type)

    if event_type == "context.usage.updated":
        allowed = _STREAM_EVENT_FIELDS | {"context_usage"}
        if set(raw) - allowed or raw.get("display") is not None:
            raise ValueError("context.usage.updated contains unsupported fields or display")
        snapshot = raw.get("context_usage")
        if not isinstance(snapshot, dict):
            raise ValueError("context.usage.updated requires context_usage")
        return ContextUsageUpdatedEvent(**common, snapshot=parse_context_usage(snapshot))

    if event_type == "tool.calls.start":
        return ToolCallsStartEvent(
            **common,
            **_tool_calls_start_fields(raw),
        )
    if event_type == "tool.calls.done":
        return ToolCallsDoneEvent(
            **common,
            **_tool_calls_done_fields(raw),
        )

    if event_type in _MARKER_EVENT_TYPES:
        return MarkerEvent(**common)
    if event_type == "turn.completed":
        event_seq = common["event_seq"]
        if not isinstance(event_seq, int):
            raise ValueError("turn.completed requires event_seq")
        return TurnCompletedEvent(
            **common,
            **_terminal_fields(raw),
            **_turn_completed_fields(raw, event_seq=event_seq),
        )
    if event_type == "turn.retrying":
        retry_round = _required_positive_int(
            common.get("round"),
            "turn.retrying round",
        )
        attempt = _required_positive_int(
            raw.get("attempt"),
            "turn.retrying attempt",
        )
        if attempt < 2:
            raise ValueError("turn.retrying attempt must be greater than one")
        max_attempts = _required_positive_int(
            raw.get("max_attempts"),
            "turn.retrying max_attempts",
        )
        if attempt > max_attempts:
            raise ValueError("turn.retrying attempt exceeds max_attempts")

        retry_in_ms = _nonnegative_int(raw.get("retry_in_ms"))
        if retry_in_ms is None:
            raise ValueError("turn.retrying retry_in_ms must be a non-negative integer")

        return TurnRetryingEvent(
            **{**common, "round": retry_round},
            attempt=attempt,
            max_attempts=max_attempts,
            retry_in_ms=retry_in_ms,
            reason=_text(raw.get("reason")) or "stream_error",
            supersedes_item_id=_text(raw.get("supersedes_item_id")),
            **_retry_failure_fields(raw),
        )
    if event_type == "turn.input.accepted":
        return TurnInputAcceptedEvent(
            **common,
            client_message_id=_text(raw.get("client_message_id")),
        )
    if event_type == "session.title.updated":
        return _session_title_updated_event(raw, common)
    if event_type == "turn.reconciliation_required":
        return TurnReconciliationRequiredEvent(
            **common,
            effect_id=_required_text(
                raw.get("effect_id"),
                "turn.reconciliation_required effect_id",
            ),
            error=_error_text(raw.get("error")),
        )
    if event_type == "presentation.superseded":
        superseded_epoch = _required_positive_int(
            raw.get("superseded_epoch"),
            "presentation.superseded superseded_epoch",
        )
        if superseded_epoch >= common["presentation_epoch"]:
            raise ValueError(
                "presentation.superseded epoch must precede presentation_epoch"
            )
        return PresentationSupersededEvent(
            **common,
            superseded_epoch=superseded_epoch,
            reason=_text(raw.get("reason")) or "attempt_restarted",
        )
    if event_type in {
        "context.compaction.started",
        "context.compaction.completed",
        "context.compaction.failed",
    }:
        return _context_compaction_event(
            raw,
            common,
            event_type=event_type,
        )
    if event_type in {
        "review.started",
        "review.completed",
        "review.failed",
        "review.cancelled",
        "review.reconciliation_required",
    }:
        return _review_event(raw, common, event_type=event_type)
    if event_type == "text.delta":
        segment_id = _required_text(
            raw.get("segment_id"),
            "text.delta segment_id",
        )
        return TextDeltaEvent(
            **common,
            **_item_fields(
                raw,
                event_type=event_type,
                source_id=segment_id,
                expected_kind="text",
                expected_status="in_progress",
            ),
            text=str(raw.get("text") or ""),
            segment_id=segment_id,
            phase=_assistant_text_phase(raw.get("phase")),
        )
    if event_type == "text.done":
        segment_id = _required_text(
            raw.get("segment_id"),
            "text.done segment_id",
        )
        return TextDoneEvent(
            **common,
            **_item_fields(
                raw,
                event_type=event_type,
                source_id=segment_id,
                expected_kind="text",
                expected_status="completed",
            ),
            segment_id=segment_id,
            final_text=_optional_content_text(raw.get("final_text")),
            phase=_assistant_text_phase(raw.get("phase")),
        )
    if event_type == "text.meta":
        segment_id = _required_text(
            raw.get("segment_id"),
            "text.meta segment_id",
        )
        return TextMetaEvent(
            **common,
            **_item_fields(
                raw,
                event_type=event_type,
                source_id=segment_id,
                expected_kind="text",
                expected_status="completed",
            ),
            segment_id=segment_id,
            phase=_assistant_text_phase(raw.get("phase")),
            annotations=_tuple_or_none(raw.get("annotations")),
            citations=_tuple_or_none(raw.get("citations")),
            sources=_tuple_or_none(raw.get("sources")),
            source_count=_nonnegative_int(raw.get("source_count")),
            builtin_call_ids=_text_tuple_or_none(raw.get("builtin_call_ids")),
        )
    if event_type == "tool.builtin.call":
        builtin_call_id = _text(raw.get("builtin_call_id"))
        return ToolBuiltinCallEvent(
            **common,
            **_item_fields(
                raw,
                event_type=event_type,
                source_id=builtin_call_id,
                expected_kind="builtin_tool",
                expected_status="in_progress",
            ),
            builtin_call_id=builtin_call_id,
            builtin_type=_text(raw.get("builtin_type")),
            payload=raw,
        )
    if event_type == "tool.builtin.done":
        builtin_call_id = _text(raw.get("builtin_call_id"))
        return ToolBuiltinDoneEvent(
            **common,
            **_item_fields(
                raw,
                event_type=event_type,
                source_id=builtin_call_id,
                expected_kind="builtin_tool",
                expected_status=_builtin_done_item_status(raw.get("status")),
            ),
            builtin_call_id=builtin_call_id,
            builtin_type=_text(raw.get("builtin_type")),
            sources=_tuple_or_none(raw.get("sources")),
            source_count=_nonnegative_int(raw.get("source_count")),
        )
    if event_type in {
        "tool.approval_review.started",
        "tool.approval_review.completed",
    }:
        return _approval_review_event(raw, common, event_type=event_type)
    if event_type == "tool.approval_required":
        raw_decisions = raw.get("available_decisions")
        if not isinstance(raw_decisions, list) or not raw_decisions:
            raise ValueError(
                "tool.approval_required available_decisions is required"
            )
        if (
            len(raw_decisions) > 5
            or any(not isinstance(item, str) for item in raw_decisions)
            or len(set(raw_decisions)) != len(raw_decisions)
        ):
            raise ValueError(
                "tool.approval_required available_decisions must be unique and contain at most five items"
            )
        kind = _approval_kind(raw.get("kind"))
        removed_fields = {"name", "approval", "patch_scope"}
        if kind != "mcp_tool_call":
            removed_fields.add("arguments")
        present_removed = sorted(
            removed_field
            for removed_field in removed_fields
            if removed_field in raw
        )
        if present_removed:
            raise ValueError(
                "tool.approval_required contains removed protocol fields: "
                + ", ".join(present_removed)
            )
        available_decisions: list[ToolApprovalDecision] = []
        for raw_decision in raw_decisions:
            decision = _required_text(
                raw_decision,
                "tool.approval_required available_decisions item",
            )
            if decision not in TOOL_APPROVAL_DECISIONS:
                raise ValueError(
                    f"unsupported tool approval decision: {decision}"
                )
            if decision not in TOOL_APPROVAL_DECISIONS_BY_KIND[kind]:
                raise ValueError(
                    f"tool.approval_required decision is invalid for {kind}"
                )
            typed_decision = _approval_decision(decision)
            if typed_decision is None:
                raise ValueError(
                    f"unsupported tool approval decision: {decision}"
                )
            available_decisions.append(typed_decision)
        if "reason" not in raw or not isinstance(raw.get("reason"), str):
            raise ValueError("tool.approval_required reason is required")

        if kind == "apply_patch":
            patch = raw.get("patch")
            if not isinstance(patch, str) or not patch.strip():
                raise ValueError(
                    "tool.approval_required apply_patch patch is required"
                )
        else:
            patch = raw.get("patch", "")

        approval_id = _required_text(
            raw.get("approval_id"),
            "tool.approval_required approval_id",
        )
        status = _approval_status(raw.get("status"))
        if "ack" not in raw:
            raise ValueError("tool.approval_required ack is required")
        ack = raw.get("ack")
        if ack is not None and not isinstance(ack, dict):
            raise ValueError("tool.approval_required ack must be an object or null")
        if status == "resolved" and ack is None:
            raise ValueError("resolved tool approval requires ack")
        if status != "resolved" and ack is not None:
            raise ValueError("only resolved tool approval may contain ack")
        _validate_approval_ack(
            ack,
            kind=kind,
            available_decisions=available_decisions,
            envelope=raw,
        )

        action_fields = _approval_action_fields(raw, kind)
        if kind == "request_permissions":
            action_fields["scope"] = _optional_text(
                ack.get("scope") if isinstance(ack, dict) else None
            )
            action_fields["strict_auto_review"] = (
                ack.get("strict_auto_review")
                if isinstance(ack, dict)
                   and isinstance(ack.get("strict_auto_review"), bool)
                else None
            )

        files = tuple(action_fields.pop("files", ()))
        permissions_preapproved = action_fields.pop("permissions_preapproved", None)
        scope = action_fields.pop("scope", None)
        strict_auto_review = action_fields.pop("strict_auto_review", None)

        parsed_cmd = (
            tuple(copy.deepcopy(raw["parsed_cmd"]))
            if kind == "command"
            else ()
        )
        return ToolApprovalRequiredEvent(
            **common,
            **_item_fields(
                raw,
                event_type=event_type,
                source_id=approval_id,
                expected_kind="approval",
                expected_status="waiting_approval",
            ),
            call_id=_required_text(raw.get("call_id"), "tool.approval_required call_id"),
            kind=kind,
            approval_id=approval_id,
            status=status,
            ack=_optional_dict(ack),
            environment_id=_optional_text(raw.get("environment_id")),
            started_at_ms=_approval_started_at(raw),
            plugin_id=_optional_text(raw.get("plugin_id")),
            script_path=_optional_text(raw.get("script_path")),
            tty=_bool_value(raw.get("tty")),
            sandbox_permissions=_approval_sandbox_permissions(raw, kind),
            additional_permissions=_optional_dict(
                raw.get("additional_permissions")
            ),
            policy_fingerprint=_optional_text(raw.get("policy_fingerprint")),
            patch_scope=files,
            files=files,
            permissions_preapproved=permissions_preapproved,
            command=raw.get("command", ""),
            patch=patch,
            cwd=(
                _optional_text(raw.get("cwd"))
                if kind not in {"command", "apply_patch", "network_access"}
                else _required_text(
                    raw.get("cwd"),
                    "tool.approval_required cwd",
                )
            ),
            cwd_raw=(
                _optional_text(raw.get("cwd_raw"))
                if kind not in {"command", "apply_patch", "network_access"}
                else _required_text(
                    raw.get("cwd_raw"),
                    "tool.approval_required cwd_raw",
                )
            ),
            reason=_text(raw.get("reason")),
            proposed_execpolicy_amendment=_optional_dict(
                raw.get("proposed_execpolicy_amendment")
            ),
            scope=scope,
            strict_auto_review=strict_auto_review,
            **action_fields,
            available_decisions=tuple(available_decisions),
            parsed_cmd=parsed_cmd,
        )
    if event_type == "tool.call":
        if "tool" in raw:
            raise ValueError("tool.call contains removed protocol field: tool")
        tool_fields = _tool_fields(raw)
        if not tool_fields["name"]:
            raise ValueError("tool.call name is required")
        if not tool_fields["call_id"]:
            raise ValueError("tool.call call_id is required")
        return ToolCallEvent(
            **common,
            **_item_fields(
                raw,
                event_type=event_type,
                source_id=tool_fields["call_id"],
                expected_kind="tool_call",
                expected_status="waiting_result",
            ),
            **tool_fields,
        )

    if event_type == "tool.output":
        if "tool" in raw:
            raise ValueError("tool.output contains removed protocol field: tool")
        tool_fields = _tool_fields(raw)
        call_id = _required_text(tool_fields["call_id"], "tool.output call_id")
        return ToolOutputEvent(
            **common,
            **_item_fields(
                raw,
                event_type=event_type,
                source_id=f"{call_id}:output",
                expected_kind="tool_output",
                expected_status=_tool_output_item_status(raw.get("status")),
            ),
            **tool_fields,
            payload=raw,
        )

    _reject_item_projection(raw, event_type=event_type)
    return UnknownStreamEvent(**common, payload=raw)


def parse_compact_event(
    payload: Mapping[str, JsonValue],
) -> ContextCompactionEvent | ContextUsageUpdatedEvent:
    """校验手动压缩端点的 Item；操作失败事件允许没有持久化序号。"""
    raw = dict(payload)
    event_type = _required_text(raw.get("type"), "compact event type")
    if event_type == "context.usage.updated":
        event = parse_stream_event(payload)
        if not isinstance(event, ContextUsageUpdatedEvent):
            raise TypeError("compact context usage event is invalid")
        return event
    if event_type not in {
        "context.compaction.started",
        "context.compaction.completed",
        "context.compaction.failed",
    }:
        raise ValueError("unexpected compact event type")
    proto = _required_text(raw.get("proto"), "proto")
    if proto != "mind.chat":
        raise ValueError("compact event proto must be mind.chat")
    event = _context_compaction_event(
        raw,
        {
            "type": event_type,
            "proto": proto,
            "cid": _required_text(raw.get("cid"), "cid"),
            "sid": _required_text(raw.get("sid"), "sid"),
            "turn_id": _session_turn_id(raw.get("turn_id")),
            "event_seq": _event_sequence(raw),
            "presentation_epoch": _required_positive_int(
                raw.get("presentation_epoch"), "presentation_epoch",
            ),
        },
        event_type=event_type,
    )
    if event.phase != "standalone" or event.trigger != "manual":
        raise ValueError("compact endpoint requires a standalone manual event")
    return event


def _context_compaction_event(
    payload: dict[str, typing.Any],
    common: dict[str, typing.Any],
    *,
    event_type: str,
) -> ContextCompactionEvent:
    """校验上下文压缩 Item 的阶段、来源和终态载荷。"""
    removed_fields = sorted(
        field_name
        for field_name in ("summary", "compaction_generation")
        if field_name in payload
    )
    if removed_fields:
        raise ValueError(
            f"{event_type} contains removed protocol fields: "
            + ", ".join(removed_fields)
        )

    expected_status: ItemStatus = {
        "context.compaction.started": "in_progress",
        "context.compaction.completed": "completed",
        "context.compaction.failed": "failed",
    }[event_type]
    item_fields = _item_fields(
        payload,
        event_type=event_type,
        source_id="",
        expected_kind="context_compaction",
        expected_status=expected_status,
    )

    raw_phase = _required_text(payload.get("phase"), f"{event_type} phase")
    if raw_phase not in {"pre_turn", "mid_turn", "standalone"}:
        raise ValueError(f"{event_type} phase is invalid")
    phase: ContextCompactionPhase = raw_phase

    raw_trigger = _required_text(
        payload.get("trigger"),
        f"{event_type} trigger",
    )
    if raw_trigger not in {"automatic", "manual"}:
        raise ValueError(f"{event_type} trigger is invalid")
    trigger: ContextCompactionTrigger = raw_trigger

    error_type = _optional_text(payload.get("error_type"))
    retryable = payload.get("retryable")
    if retryable is not None and not isinstance(retryable, bool):
        raise ValueError(f"{event_type} retryable must be a boolean")
    if event_type == "context.compaction.failed":
        error_type = _required_text(
            payload.get("error_type"),
            "context.compaction.failed error_type",
        )
        retryable = _required_bool(
            payload.get("retryable"),
            "context.compaction.failed retryable",
        )

    return ContextCompactionEvent(
        **common,
        **item_fields,
        phase=phase,
        trigger=trigger,
        reason=_required_text(payload.get("reason"), f"{event_type} reason"),
        error_type=error_type,
        retryable=retryable,
        before_items=_optional_nonnegative_int(
            payload.get("before_items"),
            f"{event_type} before_items",
        ),
        after_items=_optional_nonnegative_int(
            payload.get("after_items"),
            f"{event_type} after_items",
        ),
        before_chars=_optional_nonnegative_int(
            payload.get("before_chars"),
            f"{event_type} before_chars",
        ),
        after_chars=_optional_nonnegative_int(
            payload.get("after_chars"),
            f"{event_type} after_chars",
        ),
        dropped_items=_optional_nonnegative_int(
            payload.get("dropped_items"),
            f"{event_type} dropped_items",
        ),
        reduction_ratio=_optional_ratio(
            payload.get("reduction_ratio"),
            f"{event_type} reduction_ratio",
        ),
        latency_ms=_optional_nonnegative_int(
            payload.get("latency_ms"),
            f"{event_type} latency_ms",
        ),
        replacement_version=_optional_nonnegative_int(
            payload.get("replacement_version"),
            f"{event_type} replacement_version",
        ),
    )


_STREAM_EVENT_FIELDS = frozenset({
    "type",
    "proto",
    "cid",
    "sid",
    "turn_id",
    "event_seq",
    "round",
    "presentation_epoch",
    "display",
    "event_id",
    "correlation_id",
    "causation_id",
    "occurred_at",
    "created_at",
    "idempotency_key",
    "ts",
})

_ITEM_EVENT_FIELDS = _STREAM_EVENT_FIELDS | frozenset({
    "item_id",
    "item_kind",
    "item_status",
})

_SESSION_TITLE_UPDATED_FIELDS = _STREAM_EVENT_FIELDS | {"title"}


def _session_title_updated_event(
    payload: dict[str, typing.Any],
    common: dict[str, typing.Any],
) -> SessionTitleUpdatedEvent:
    """严格解析服务端提交的会话标题更新。"""
    unknown = sorted(set(payload).difference(_SESSION_TITLE_UPDATED_FIELDS))
    if unknown:
        raise ValueError(
            "session.title.updated contains unknown fields: "
            + ", ".join(unknown)
        )
    title = _required_text(
        payload.get("title"),
        "session.title.updated title",
    )
    if len(title) > 500:
        raise ValueError(
            "session.title.updated title must contain at most 500 characters"
        )
    if not title.isprintable():
        raise ValueError("session.title.updated title must be printable")
    return SessionTitleUpdatedEvent(**common, title=title)


def _review_event(
    payload: dict[str, typing.Any],
    common: dict[str, typing.Any],
    *,
    event_type: str,
) -> (
    ReviewStartedEvent
    | ReviewCompletedEvent
    | ReviewFailedEvent
    | ReviewCancelledEvent
    | ReviewReconciliationRequiredEvent
):
    """严格解析 Review Item 生命周期事件。"""
    expected_status: ItemStatus = {
        "review.started": "in_progress",
        "review.completed": "completed",
        "review.failed": "failed",
        "review.cancelled": "cancelled",
        "review.reconciliation_required": "reconciliation_required",
    }[event_type]
    review_item_id = _required_text(
        payload.get("review_item_id"),
        f"{event_type} review_item_id",
    )
    item_fields = _item_fields(
        payload,
        event_type=event_type,
        source_id=review_item_id,
        expected_kind="review",
        expected_status=expected_status,
    )
    status = _required_text(payload.get("status"), f"{event_type} status")
    if status != expected_status:
        raise ValueError(f"{event_type} status does not match event type")

    event_fields = {"review_item_id", "status"}
    if event_type == "review.started":
        event_fields.update({"target", "workspace_revision", "prompt_version"})
    elif event_type == "review.completed":
        event_fields.add("output")
    elif event_type == "review.failed":
        event_fields.update({"error", "output_preview"})
    elif event_type == "review.cancelled":
        event_fields.add("reason")
    else:
        event_fields.update({"effect_id", "error"})
    unknown = sorted(set(payload).difference(_ITEM_EVENT_FIELDS | event_fields))
    if unknown:
        raise ValueError(
            f"{event_type} contains unknown fields: " + ", ".join(unknown)
        )

    if event_type == "review.started":
        revision = _required_text(
            payload.get("workspace_revision"),
            "review.started workspace_revision",
        )
        if re.fullmatch(r"sha256:[0-9a-f]{64}", revision) is None:
            raise ValueError("review.started workspace_revision is invalid")
        prompt_version = _required_text(
            payload.get("prompt_version"),
            "review.started prompt_version",
        )
        if prompt_version != "mind-review/1":
            raise ValueError("review.started prompt_version is unsupported")
        target_value = payload.get("target")
        if not _is_json_value(target_value):
            raise ValueError("review.started target must be a JSON value")
        return ReviewStartedEvent(
            **common,
            **item_fields,
            review_item_id=review_item_id,
            status="in_progress",
            target=parse_review_target(target_value),
            workspace_revision=revision,
            prompt_version=prompt_version,
        )
    if event_type == "review.completed":
        output_value = payload.get("output")
        if not _is_json_value(output_value):
            raise ValueError("review.completed output must be a JSON value")
        return ReviewCompletedEvent(
            **common,
            **item_fields,
            review_item_id=review_item_id,
            status="completed",
            output=parse_review_output(output_value),
        )
    if event_type == "review.failed":
        output_preview = payload.get("output_preview", "")
        if not isinstance(output_preview, str):
            raise ValueError("review.failed output_preview must be text")
        return ReviewFailedEvent(
            **common,
            **item_fields,
            review_item_id=review_item_id,
            status="failed",
            error=_required_text(payload.get("error"), "review.failed error"),
            output_preview=output_preview,
        )
    if event_type == "review.cancelled":
        return ReviewCancelledEvent(
            **common,
            **item_fields,
            review_item_id=review_item_id,
            status="cancelled",
            reason=_required_text(
                payload.get("reason"),
                "review.cancelled reason",
            ),
        )
    return ReviewReconciliationRequiredEvent(
        **common,
        **item_fields,
        review_item_id=review_item_id,
        status="reconciliation_required",
        effect_id=_required_text(
            payload.get("effect_id"),
            "review.reconciliation_required effect_id",
        ),
        error=_required_text(
            payload.get("error"),
            "review.reconciliation_required error",
        ),
    )


def _approval_review_event(
    payload: dict[str, typing.Any],
    common: dict[str, typing.Any],
    *,
    event_type: str,
) -> ToolApprovalReviewStartedEvent | ToolApprovalReviewCompletedEvent:
    """校验自动审批评审事件及其状态组合。"""
    allowed_fields = _ITEM_EVENT_FIELDS | {
        "review_id",
        "approval_id",
        "call_id",
        "target_item_id",
        "kind",
        "action",
        "review",
        "started_at_ms",
        "completed_at_ms",
        "decision_source",
    }
    unknown_fields = sorted(set(payload) - allowed_fields)
    if unknown_fields:
        raise ValueError(
            f"{event_type} contains unknown fields: "
            + ", ".join(unknown_fields)
        )

    review_id = _required_text(payload.get("review_id"), f"{event_type} review_id")
    approval_id = _required_text(
        payload.get("approval_id"),
        f"{event_type} approval_id",
    )
    call_id = _required_text(payload.get("call_id"), f"{event_type} call_id")
    kind = _approval_kind(payload.get("kind"))
    action = payload.get("action")
    if not isinstance(action, dict) or not action:
        raise ValueError(f"{event_type} action must be a non-empty object")
    if not _is_json_value(action):
        raise ValueError(f"{event_type} action must contain only JSON values")

    started_at_ms = _nonnegative_int(payload.get("started_at_ms"))
    if started_at_ms is None:
        raise ValueError(f"{event_type} started_at_ms must be non-negative")
    target_item_id = _required_text(
        payload.get("target_item_id"),
        f"{event_type} target_item_id",
    )
    review = _approval_review(payload.get("review"), event_type=event_type)
    expected_status = _approval_review_item_status(review.status)
    item_fields = _item_fields(
        payload,
        event_type=event_type,
        source_id=review_id,
        expected_kind="approval",
        expected_status=expected_status,
    )
    shared = {
        **common,
        **item_fields,
        "review_id": review_id,
        "approval_id": approval_id,
        "call_id": call_id,
        "target_item_id": target_item_id,
        "kind": kind,
        "action": action,
        "review": review,
        "started_at_ms": started_at_ms,
    }

    if event_type == "tool.approval_review.started":
        if review.status != "in_progress":
            raise ValueError("started approval review must be in_progress")
        if "completed_at_ms" in payload or "decision_source" in payload:
            raise ValueError("started approval review contains terminal fields")
        return ToolApprovalReviewStartedEvent(**shared)

    if review.status == "in_progress":
        raise ValueError("completed approval review must be terminal")
    completed_at_ms = _nonnegative_int(payload.get("completed_at_ms"))
    if completed_at_ms is None:
        raise ValueError(f"{event_type} completed_at_ms must be non-negative")
    if completed_at_ms < started_at_ms:
        raise ValueError("approval review completed_at_ms precedes started_at_ms")
    if payload.get("decision_source") != "agent":
        raise ValueError("completed approval review decision_source must be agent")
    return ToolApprovalReviewCompletedEvent(
        **shared,
        completed_at_ms=completed_at_ms,
        decision_source="agent",
    )


def _approval_review(
    value: typing.Any,
    *,
    event_type: str,
) -> ApprovalReview:
    """校验自动审批 reviewer 的状态载荷。"""
    if not isinstance(value, dict):
        raise ValueError(f"{event_type} review must be an object")
    unknown_fields = sorted(
        set(value) - {"status", "risk_level", "user_authorization", "rationale"}
    )
    if unknown_fields:
        raise ValueError(
            f"{event_type} review contains unknown fields: "
            + ", ".join(unknown_fields)
        )
    raw_status = _required_text(value.get("status"), f"{event_type} review status")
    if raw_status not in {
        "in_progress",
        "approved",
        "denied",
        "timed_out",
        "aborted",
    }:
        raise ValueError(f"{event_type} review status is invalid")
    status: ApprovalReviewStatus = raw_status

    raw_risk = value.get("risk_level")
    risk_level: ApprovalReviewRiskLevel | None = None
    if raw_risk is not None:
        if raw_risk not in {"low", "medium", "high", "critical"}:
            raise ValueError(f"{event_type} review risk_level is invalid")
        risk_level = raw_risk

    raw_authorization = value.get("user_authorization")
    user_authorization: ApprovalReviewUserAuthorization | None = None
    if raw_authorization is not None:
        if raw_authorization not in {"unknown", "low", "medium", "high"}:
            raise ValueError(f"{event_type} review user_authorization is invalid")
        user_authorization = raw_authorization

    raw_rationale = value.get("rationale")
    if raw_rationale is not None and not isinstance(raw_rationale, str):
        raise ValueError(f"{event_type} review rationale must be text or null")
    rationale = raw_rationale.strip() if isinstance(raw_rationale, str) else None
    if rationale == "":
        rationale = None

    if status == "in_progress":
        if any(item is not None for item in (risk_level, user_authorization, rationale)):
            raise ValueError("in_progress approval review cannot contain a decision")
    elif status in {"approved", "denied"}:
        if risk_level is None or user_authorization is None or rationale is None:
            raise ValueError(
                f"{status} approval review requires risk, authorization and rationale"
            )
    elif status == "timed_out":
        if risk_level is not None or user_authorization is not None or rationale is None:
            raise ValueError(
                "timed_out approval review requires only a rationale"
            )
    elif risk_level is not None or user_authorization is not None:
        raise ValueError("aborted approval review cannot contain a risk decision")

    return ApprovalReview(
        status=status,
        risk_level=risk_level,
        user_authorization=user_authorization,
        rationale=rationale,
    )


def _approval_review_item_status(status: ApprovalReviewStatus) -> ItemStatus:
    """把自动评审状态映射为 Canonical Item 生命周期。"""
    if status == "in_progress":
        return "in_progress"
    if status == "timed_out":
        return "failed"
    if status == "aborted":
        return "cancelled"
    return "completed"


def _common_fields(
    payload: dict[str, typing.Any],
    event_type: str
) -> dict[str, typing.Any]:
    """提取所有流式事件共享的字段。"""
    if event_type not in _ITEM_EVENT_TYPES:
        _reject_item_projection(payload, event_type=event_type)

    display = payload.get("display")
    if event_type == "ping":
        proto = _text(payload.get("proto"))
        cid = _text(payload.get("cid"))
        sid = _text(payload.get("sid"))
        turn_id = _text(payload.get("turn_id"))
        event_seq = _event_sequence(payload)
        presentation_epoch = _positive_int(
            payload.get("presentation_epoch")
        ) or 1

    else:
        proto = _required_text(payload.get("proto"), "proto")
        if proto != "mind.chat":
            raise ValueError("stream event proto must be mind.chat")
        cid = _required_text(payload.get("cid"), "cid")
        sid = _required_text(payload.get("sid"), "sid")
        turn_id = (
            _session_turn_id(payload.get("turn_id"))
            if event_type == "context.usage.updated" or (
                event_type.startswith("context.compaction.") and payload.get("phase") == "standalone"
            )
            else _required_text(payload.get("turn_id"), "turn_id")
        )
        event_seq = _required_positive_int(payload.get("event_seq"), "event_seq")
        presentation_epoch = _required_positive_int(
            payload.get("presentation_epoch"),
            "presentation_epoch",
        )

    return {
        "type": event_type,
        "proto": proto,
        "cid": cid,
        "sid": sid,
        "turn_id": turn_id,
        "event_seq": event_seq,
        "round": _positive_int(payload.get("round")),
        "presentation_epoch": presentation_epoch,
        "display": copy.deepcopy(display) if isinstance(display, dict) else None
    }


def _session_turn_id(value: JsonValue) -> str:
    """保留 Session 事件的空 Turn 身份，拒绝缺失、非字符串或空白别名。"""
    if not isinstance(value, str) or value != value.strip():
        raise ValueError("session event turn_id must be a string")
    return value


def _tool_fields(payload: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """提取工具事件共享的调用字段。"""
    return {
        "name": _text(payload.get("name")),
        "call_id": _text(payload.get("call_id")),
        "arguments": _dict(payload.get("arguments")),
        "reason": _text(payload.get("reason")),
    }


def _tool_calls_boundary_identity(
    payload: dict[str, typing.Any],
    event_type: str,
) -> dict[str, typing.Any]:
    """读取并校验客户端工具批次共享身份字段。"""
    batch_id = _required_text(payload.get("batch_id"), f"{event_type} batch_id")
    raw_call_ids = payload.get("call_ids")
    if not isinstance(raw_call_ids, list) or not raw_call_ids:
        raise ValueError(f"{event_type} call_ids must be a non-empty list")
    call_ids = tuple(
        _required_text(value, f"{event_type} call_ids")
        for value in raw_call_ids
    )
    if len(set(call_ids)) != len(call_ids):
        raise ValueError(f"{event_type} call_ids must be unique")
    count = _required_positive_int(payload.get("count"), f"{event_type} count")
    if count != len(call_ids):
        raise ValueError(f"{event_type} count does not match call_ids")
    return {
        "batch_id": batch_id,
        "call_ids": call_ids,
        "count": count,
    }


def _tool_calls_start_fields(
    payload: dict[str, typing.Any],
) -> dict[str, typing.Any]:
    """读取仅属于 tool.calls.start 的就绪与执行预算字段。"""
    fields = _tool_calls_boundary_identity(payload, "tool.calls.start")
    ready = _required_bool(payload.get("ready"), "tool.calls.start ready")
    if not ready:
        raise ValueError("tool.calls.start ready must be true")
    timeout_sec = payload.get("timeout_sec")
    if timeout_sec is not None:
        timeout_sec = _required_positive_int(
            timeout_sec,
            "tool.calls.start timeout_sec",
        )
    return {
        **fields,
        "ready": True,
        "timeout_sec": timeout_sec,
    }


def _tool_calls_done_fields(
    payload: dict[str, typing.Any],
) -> dict[str, typing.Any]:
    """读取 tool.calls.done 的完整批次身份。"""
    return _tool_calls_boundary_identity(payload, "tool.calls.done")


def _stream_gap_event(payload: dict[str, typing.Any]) -> StreamGapEvent:
    """解析不占用持久事件序号的回放缺口信号。"""
    _reject_item_projection(payload, event_type="stream.gap")
    gap_kind = _required_text(payload.get("gap_kind"), "stream.gap gap_kind")
    if gap_kind not in {"retained_prefix", "internal"}:
        raise ValueError("stream.gap gap_kind is invalid")

    requested_after_seq = _nonnegative_int(payload.get("requested_after_seq"))
    if requested_after_seq is None:
        raise ValueError("stream.gap requested_after_seq must be non-negative")

    first_event_seq = _positive_int(payload.get("first_event_seq"))
    next_seq = _nonnegative_int(payload.get("next_seq"))
    expected_event_seq = _positive_int(payload.get("expected_event_seq"))
    observed_event_seq = _positive_int(payload.get("observed_event_seq"))

    if gap_kind == "retained_prefix":
        if first_event_seq is None or next_seq is None:
            raise ValueError("retained_prefix stream.gap requires replay floor")
        if next_seq != first_event_seq - 1 or next_seq < requested_after_seq:
            raise ValueError("retained_prefix stream.gap replay floor is invalid")
    elif expected_event_seq is None or observed_event_seq is None:
        raise ValueError("internal stream.gap requires sequence coordinates")

    return StreamGapEvent(
        type="stream.gap",
        cid=_required_text(payload.get("cid"), "stream.gap cid"),
        sid=_required_text(payload.get("sid"), "stream.gap sid"),
        turn_id=_required_text(payload.get("turn_id"), "stream.gap turn_id"),
        gap_kind=gap_kind,
        requested_after_seq=requested_after_seq,
        first_event_seq=first_event_seq,
        next_seq=next_seq,
        expected_event_seq=expected_event_seq,
        observed_event_seq=observed_event_seq,
        replay_required=_bool_value(payload.get("replay_required")),
        retryable=_bool_value(payload.get("retryable")),
    )


def _terminal_fields(payload: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """提取轮次终态共享的响应元数据。"""
    return {
        "response_id": _text(payload.get("response_id")),
        "model": _text(payload.get("model")),
        "route": _text(payload.get("route")),
        "request_id": _text(payload.get("request_id")),
        "service_tier": _text(payload.get("service_tier")),
        "usage": _dict(payload.get("usage")),
        "stop_reason": _optional_text(payload.get("stop_reason")),
        "stop_sequence": _optional_text(payload.get("stop_sequence")),
    }


def _failure_fields(payload: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """读取失败事件中可选的错误分类和传输元数据。"""
    error = payload.get("error")
    error_object = error if isinstance(error, Mapping) else {}
    top_error_type = _text(payload.get("error_type"))
    nested_error_type = _text(error_object.get("type"))
    if top_error_type and nested_error_type and top_error_type != nested_error_type:
        raise ValueError("failure error_type does not match error.type")

    retryable: bool | None = None
    if "retryable" in error_object:
        retryable = _required_bool(
            error_object.get("retryable"),
            "failure error.retryable",
        )
    elif "retryable" in payload:
        retryable = _required_bool(
            payload.get("retryable"),
            "failure retryable",
        )

    status_code = payload.get("status_code")
    if status_code is not None:
        status_code = _required_positive_int(status_code, "failure status_code")

    return {
        "error_type": top_error_type or nested_error_type,
        "error_source": (
            _text(error_object.get("source"))
            or _text(payload.get("error_source"))
        ),
        "status_code": status_code,
        "retryable": retryable,
    }


def _retry_failure_fields(payload: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """读取并校验新协议中 turn.retrying 的错误信封。"""
    if not any(
        key in payload
        for key in ("error_type", "error")
    ):
        return {
            "error_type": "",
            "error_source": "",
            "retryable": None,
        }

    error_type = _required_text(
        payload.get("error_type"),
        "turn.retrying error_type",
    )
    error = payload.get("error")
    if not isinstance(error, Mapping):
        raise ValueError("turn.retrying error must be an object")
    nested_type = _required_text(
        error.get("type"),
        "turn.retrying error.type",
    )
    if nested_type != error_type:
        raise ValueError("turn.retrying error_type does not match error.type")
    source = _required_text(
        error.get("source"),
        "turn.retrying error.source",
    )
    retryable = _required_bool(
        error.get("retryable"),
        "turn.retrying error.retryable",
    )
    return {
        "error_type": error_type,
        "error_source": source,
        "retryable": retryable,
    }


def _required_bool(value: typing.Any, field_name: str) -> bool:
    """读取必填布尔协议值。"""
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean")
    return value


def _optional_nonnegative_int(
    value: typing.Any,
    field_name: str,
) -> int | None:
    """读取可选非负整数并保留缺失值。"""
    if value is None:
        return None
    parsed = _nonnegative_int(value)
    if parsed is None:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return parsed


def _optional_ratio(value: typing.Any, field_name: str) -> float | None:
    """读取可选的零到一压缩比例。"""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a number between zero and one")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0 or parsed > 1:
        raise ValueError(f"{field_name} must be a number between zero and one")
    return parsed


def _turn_completed_status(value: typing.Any) -> TurnCompletedStatus:
    """读取唯一 Turn 终态事件的受支持状态。"""
    status = _required_text(value, "turn.completed status")
    try:
        return parse_turn_completed_status(status)
    except ValueError as error:
        raise ValueError(f"unsupported turn.completed status: {status}") from error


def _turn_completed_fields(
    payload: dict[str, typing.Any],
    *,
    event_seq: int,
) -> dict[str, typing.Any]:
    """校验唯一终态的状态、水位、时间、耗时和错误信封。"""
    last_event_seq = _required_positive_int(
        payload.get("last_event_seq"),
        "turn.completed last_event_seq",
    )
    if last_event_seq != event_seq:
        raise ValueError("turn.completed last_event_seq must match event_seq")

    completed_at = payload.get("completed_at")
    if (
        isinstance(completed_at, bool)
        or not isinstance(completed_at, (int, float))
        or not math.isfinite(float(completed_at))
        or float(completed_at) <= 0
    ):
        raise ValueError("turn.completed completed_at must be positive")
    duration_value = payload.get("duration_ms")
    duration_ms = (
        None
        if duration_value is None
        else _nonnegative_int(duration_value)
    )
    if duration_value is not None and duration_ms is None:
        raise ValueError("turn.completed duration_ms must be non-negative")

    status = _turn_completed_status(payload.get("status"))
    error_value = payload.get("error")
    error = "" if error_value is None else _error_text(error_value)
    if status == "failed" and not error:
        raise ValueError("turn.completed failed status requires error")

    return {
        "status": status,
        "last_event_seq": last_event_seq,
        "completed_at": float(completed_at),
        "duration_ms": duration_ms,
        "error": error,
        **_failure_fields(payload),
    }


def _error_text(value: typing.Any) -> str:
    """读取失败终态中优先展示的错误说明。"""
    if isinstance(value, Mapping):
        return _text(value.get("message")) or "unknown error"
    return _text(value) or "unknown error"


def _assistant_text_phase(value: str | None) -> AssistantTextPhase | None:
    """严格解析服务端声明的 assistant text phase。"""
    if value is None:
        return None
    if value == "commentary":
        return "commentary"
    if value == "final_answer":
        return "final_answer"
    raise ValueError("assistant text phase is invalid")


def _text(value: typing.Any) -> str:
    """把可选协议值转换为文本。"""
    return str(value or "").strip()


def _required_text(value: typing.Any, field_name: str) -> str:
    """读取必填非空文本字段。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} is required")
    return value.strip()


def _optional_text(value: typing.Any) -> str | None:
    """保留可空文本协议值。"""
    return None if value is None else str(value).strip()


def _optional_content_text(value: typing.Any) -> str | None:
    """保留正文终态中的空白字符。"""
    return None if value is None else str(value)


def _dict(value: typing.Any) -> dict[str, typing.Any]:
    """复制字典协议值。"""
    return copy.deepcopy(value) if isinstance(value, dict) else {}


def _optional_dict(value: typing.Any) -> dict[str, typing.Any] | None:
    """复制可选字典协议值。"""
    return copy.deepcopy(value) if isinstance(value, dict) else None


def _bool_value(value: typing.Any) -> bool:
    """读取协议中的布尔值。"""
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "on"}
    return bool(value)


def _tuple_or_none(value: typing.Any) -> tuple[typing.Any, ...] | None:
    """复制可选列表协议值。"""
    return tuple(copy.deepcopy(value)) if isinstance(value, list) else None


def _text_tuple_or_none(value: typing.Any) -> tuple[str, ...] | None:
    """读取可选的非空文本列表协议值。"""
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError("expected a list of text values")
    return tuple(
        _required_text(item, "list item")
        for item in value
    )


def _approval_kind(
    value: typing.Any,
) -> ToolApprovalKind:
    """读取直接审批请求的操作类型。"""
    kind = _required_text(value, "tool.approval_required kind")
    if kind == "command":
        return kind
    if kind == "write_stdin":
        return kind
    if kind == "apply_patch":
        return kind
    if kind == "network_access":
        return kind
    if kind == "request_permissions":
        return kind
    if kind == "mcp_tool_call":
        return kind
    raise ValueError("tool.approval_required kind is invalid")


def _approval_decision(value: str) -> ToolApprovalDecision | None:
    """把审批决定文本收窄为协议字面量。"""
    if value == "accept":
        return value
    if value == "acceptForSession":
        return value
    if value == "acceptWithExecpolicyAmendment":
        return value
    if value == "applyNetworkPolicyAmendment":
        return value
    if value == "grantForTurn":
        return value
    if value == "grantForTurnWithStrictAutoReview":
        return value
    if value == "grantForSession":
        return value
    if value == "decline":
        return value
    if value == "cancel":
        return value
    return None


def _approval_status(value: typing.Any) -> ToolApprovalSnapshotStatus:
    """读取审批信封的生命周期状态。"""
    status = _required_text(value, "tool.approval_required status")
    if status not in {"pending", "resolved", "expired", "cancelled"}:
        raise ValueError("tool.approval_required status is invalid")
    return status


def _approval_action_fields(
    payload: dict[str, typing.Any],
    kind: ToolApprovalKind,
) -> dict[str, typing.Any]:
    """读取并校验动作专属审批字段。"""
    fields: dict[str, typing.Any] = {}
    if kind == "command":
        _required_text(payload.get("environment_id"), "tool.approval_required environment_id")
        _required_string_list(payload.get("command"), "tool.approval_required command")
        _required_text(payload.get("cwd"), "tool.approval_required cwd")
        _required_text(payload.get("cwd_raw"), "tool.approval_required cwd_raw")
        if not isinstance(payload.get("tty"), bool):
            raise ValueError("tool.approval_required tty is required")
        if "additional_permissions" not in payload:
            raise ValueError("tool.approval_required additional_permissions is required")
        if "proposed_execpolicy_amendment" not in payload:
            raise ValueError(
                "tool.approval_required proposed_execpolicy_amendment is required"
            )
        parsed_cmd = payload.get("parsed_cmd")
        if not isinstance(parsed_cmd, list):
            raise ValueError("tool.approval_required parsed_cmd is required")
        if any(not isinstance(item, dict) for item in parsed_cmd):
            raise ValueError("tool.approval_required parsed_cmd must contain objects")
        if payload.get("sandbox_permissions") == "with_additional_permissions" and not isinstance(
            payload.get("additional_permissions"), dict
        ):
            raise ValueError(
                "tool.approval_required additional_permissions are required"
            )
        if payload.get("sandbox_permissions") != "with_additional_permissions" and payload.get(
            "additional_permissions"
        ) is not None:
            raise ValueError(
                "tool.approval_required additional_permissions are not allowed"
            )
        amendment = payload.get("proposed_execpolicy_amendment")
        has_amendment = "acceptWithExecpolicyAmendment" in {
            str(item) for item in payload.get("available_decisions", [])
        }
        if has_amendment != isinstance(amendment, dict):
            raise ValueError(
                "tool.approval_required execpolicy proposal and decision must appear together"
            )
        if isinstance(amendment, dict):
            if set(amendment) != {"command"}:
                raise ValueError(
                    "tool.approval_required execpolicy proposal is invalid"
                )
            command_prefix = amendment.get("command")
            if not isinstance(command_prefix, list) or not command_prefix or any(
                not isinstance(item, str) or not item for item in command_prefix
            ):
                raise ValueError(
                    "tool.approval_required execpolicy proposal is invalid"
                )
    elif kind == "write_stdin":
        fields["session_id"] = _required_text(
            payload.get("session_id"),
            "tool.approval_required session_id",
        )
        if "input" not in payload or not isinstance(payload.get("input"), str):
            raise ValueError("tool.approval_required input is required")
        fields["input"] = payload["input"]
        control = payload.get("control")
        if control not in {"none", "interrupt", "terminate"}:
            raise ValueError("tool.approval_required control is invalid")
        fields["control"] = control
    elif kind == "apply_patch":
        _required_text(
            payload.get("environment_id"),
            "tool.approval_required environment_id",
        )
        _required_text(payload.get("cwd"), "tool.approval_required cwd")
        _required_text(payload.get("cwd_raw"), "tool.approval_required cwd_raw")
        files = _required_string_list(
            payload.get("files"),
            "tool.approval_required files",
        )
        if "permissions_preapproved" not in payload or not isinstance(
            payload.get("permissions_preapproved"), bool
        ):
            raise ValueError(
                "tool.approval_required permissions_preapproved is required"
            )
        fields["files"] = tuple(files)
        fields["permissions_preapproved"] = payload["permissions_preapproved"]
    elif kind == "network_access":
        _required_text(
            payload.get("environment_id"),
            "tool.approval_required environment_id",
        )
        _required_text(payload.get("cwd"), "tool.approval_required cwd")
        _required_text(payload.get("cwd_raw"), "tool.approval_required cwd_raw")
        fields["target"] = _required_text(
            payload.get("target"), "tool.approval_required target"
        )
        fields["host"] = _required_text(
            payload.get("host"), "tool.approval_required host"
        )
        protocol = _required_text(
            payload.get("protocol"), "tool.approval_required protocol"
        )
        if protocol not in {"http", "https", "socks5_tcp", "socks5_udp"}:
            raise ValueError("tool.approval_required protocol is invalid")
        fields["protocol"] = protocol
        port = _required_positive_int(
            payload.get("port"), "tool.approval_required port"
        )
        if port > 65535:
            raise ValueError("tool.approval_required port is invalid")
        fields["port"] = port
        command = payload.get("command")
        if not isinstance(command, list) or not command or any(
            not isinstance(item, str) for item in command
        ):
            raise ValueError("tool.approval_required network command is required")
        if "proposed_network_policy_amendment" not in payload:
            raise ValueError(
                "tool.approval_required proposed_network_policy_amendment is required"
            )
        proposal = _optional_dict(payload.get("proposed_network_policy_amendment"))
        if proposal is not None:
            if set(proposal) != {"host", "action"}:
                raise ValueError("network policy proposal is invalid")
            if not isinstance(proposal.get("host"), str) or not proposal.get("host"):
                raise ValueError("network policy proposal host is required")
            if proposal.get("action") not in {"allow", "deny"}:
                raise ValueError("network policy proposal action is invalid")
            if proposal["host"] != fields["host"]:
                raise ValueError("network policy proposal host must match target host")
        has_amendment = "applyNetworkPolicyAmendment" in {
            str(item) for item in payload.get("available_decisions", [])
        }
        if has_amendment != (proposal is not None):
            raise ValueError("network policy proposal and decision must appear together")
        fields["proposed_network_policy_amendment"] = proposal
    elif kind == "request_permissions":
        for name in ("environment_id", "cwd"):
            raw_value = payload.get(name)
            if raw_value is not None and not isinstance(raw_value, str):
                raise ValueError(
                    f"tool.approval_required {name} must be a string or null"
                )
        raw_permissions = payload.get("permissions")
        if not isinstance(raw_permissions, dict):
            raise ValueError("tool.approval_required permissions are required")
        fields["permissions"] = copy.deepcopy(raw_permissions)
    elif kind == "mcp_tool_call":
        fields["server"] = _required_text(
            payload.get("server"), "tool.approval_required server"
        )
        fields["tool_name"] = _required_text(
            payload.get("tool_name"), "tool.approval_required tool_name"
        )
        if "arguments" not in payload:
            raise ValueError("tool.approval_required arguments are required")
        arguments = payload["arguments"]
        if not _is_json_value(arguments):
            raise ValueError("tool.approval_required arguments must be JSON")
        fields["arguments"] = copy.deepcopy(arguments)
        fields["mcp_request_id"] = _required_text(
            payload.get("mcp_request_id"), "tool.approval_required mcp_request_id"
        )
        for name in (
                "connector_id", "connector_name", "connector_description",
                "connected_account_email", "tool_title", "tool_description",
        ):
            raw_value = payload.get(name)
            if raw_value is not None and not isinstance(raw_value, str):
                raise ValueError(f"tool.approval_required {name} must be a string")
            fields[name] = _optional_text(raw_value)
        fields["annotations"] = _mcp_annotations(payload.get("annotations"))
    return fields


def _approval_sandbox_permissions(
    payload: dict[str, typing.Any],
    kind: ToolApprovalKind,
) -> str:
    """读取命令动作的沙箱权限字段。"""
    if kind != "command":
        return "use_default"
    value = payload.get("sandbox_permissions")
    if value == "use_default":
        return value
    if value == "require_escalated":
        return value
    if value == "with_additional_permissions":
        return value
    raise ValueError("tool.approval_required sandbox_permissions is invalid")


def _required_list(value: typing.Any, field_name: str) -> list[typing.Any]:
    """读取必填非空数组字段。"""
    if not isinstance(value, list) or not value:
        raise ValueError(f"{field_name} is required")
    return value


def _required_string_list(value: typing.Any, field_name: str) -> list[str]:
    """读取必填的非空字符串数组字段。"""
    values = _required_list(value, field_name)
    if any(not isinstance(item, str) or not item.strip() for item in values):
        raise ValueError(f"{field_name} must contain non-empty strings")
    return values


def _validate_approval_ack(
    value: dict[str, typing.Any] | None,
    *,
    kind: ToolApprovalKind,
    available_decisions: list[ToolApprovalDecision],
    envelope: dict[str, typing.Any] | None = None,
) -> None:
    """校验终态审批回执与动作类型及决定集合一致。"""
    if value is None:
        return
    if _required_text(value.get("kind"), "tool approval ack kind") != kind:
        raise ValueError("tool approval ack kind does not match approval")
    request_id = _required_text(value.get("request_id"), "tool approval ack request_id")
    if len(request_id) < 8:
        raise ValueError("tool approval ack request_id is too short")
    decision = _required_text(value.get("decision"), "tool approval ack decision")
    if decision not in TOOL_APPROVAL_DECISIONS_BY_KIND[kind]:
        raise ValueError("tool approval ack decision is invalid for kind")
    if decision not in available_decisions:
        raise ValueError("tool approval ack decision is not available")

    expected_tool_status = {
        "decline": "declined",
        "cancel": "cancelled",
    }.get(decision, "approved")
    if value.get("tool_status") != expected_tool_status:
        raise ValueError("tool approval ack tool_status is inconsistent")
    expected_turn_status = "interrupting" if decision == "cancel" else "active"
    if value.get("turn_status") != expected_turn_status:
        raise ValueError("tool approval ack turn_status is inconsistent")

    contexts = value.get("additional_context")
    if not isinstance(contexts, list) or any(
        not isinstance(item, str) for item in contexts
    ):
        raise ValueError("tool approval ack additional_context is invalid")
    if not isinstance(value.get("reason"), str):
        raise ValueError("tool approval ack reason is invalid")

    if kind == "command":
        amendment_id = value.get("execpolicy_amendment_id")
        if decision == "acceptWithExecpolicyAmendment":
            if not isinstance(amendment_id, str) or not amendment_id.strip():
                raise ValueError("tool approval ack execpolicy amendment is missing")
        elif amendment_id is not None:
            raise ValueError("tool approval ack execpolicy amendment is not allowed")

    if kind == "network_access":
        target = _required_text(value.get("target"), "tool approval ack target")
        host = _required_text(value.get("host"), "tool approval ack host")
        if value.get("protocol") not in {
            "http", "https", "socks5_tcp", "socks5_udp"
        }:
            raise ValueError("tool approval ack protocol is invalid")
        port = value.get("port")
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("tool approval ack port is invalid")
        if envelope is not None and (
            target != envelope.get("target")
            or host != envelope.get("host")
            or value.get("protocol") != envelope.get("protocol")
            or port != envelope.get("port")
        ):
            raise ValueError("tool approval ack network target does not match approval")
        amendment = value.get("network_policy_amendment")
        if decision == "applyNetworkPolicyAmendment":
            if not isinstance(amendment, dict):
                raise ValueError("tool approval ack network policy amendment is required")
            if (
                set(amendment) != {"host", "action"}
                or amendment.get("host") != host
                or amendment.get("action") not in {"allow", "deny"}
            ):
                raise ValueError("tool approval ack network policy host does not match")
            if envelope is not None and amendment != envelope.get(
                "proposed_network_policy_amendment"
            ):
                raise ValueError("tool approval ack network policy does not match proposal")
        elif amendment is not None:
            raise ValueError("tool approval ack network policy amendment is not allowed")
    elif kind == "request_permissions":
        granting = decision not in {"decline", "cancel"}
        scope = value.get("scope")
        permissions = value.get("permissions")
        strict_auto_review = value.get("strict_auto_review")
        if granting:
            if scope not in {"turn", "session"} or not isinstance(permissions, dict):
                raise ValueError("tool approval ack permission grant is incomplete")
            if envelope is not None and not _json_contains(
                envelope.get("permissions"), permissions
            ):
                raise ValueError("tool approval ack permissions exceed approval")
            if not isinstance(strict_auto_review, bool):
                raise ValueError("tool approval ack strict_auto_review is required")
            expected_scope = "session" if decision == "grantForSession" else "turn"
            expected_strict = decision == "grantForTurnWithStrictAutoReview"
            if scope != expected_scope or strict_auto_review is not expected_strict:
                raise ValueError("tool approval ack permission grant is inconsistent")
        elif any(item is not None for item in (scope, permissions, strict_auto_review)):
            raise ValueError("tool approval ack permission fields are not allowed")
    elif kind == "mcp_tool_call":
        server = _required_text(value.get("server"), "tool approval ack server")
        tool_name = _required_text(value.get("tool_name"), "tool approval ack tool_name")
        if "arguments" not in value:
            raise ValueError("tool approval ack arguments are required")
        if not _is_json_value(value.get("arguments")):
            raise ValueError("tool approval ack arguments must be JSON")
        if envelope is not None and (
            server != envelope.get("server")
            or tool_name != envelope.get("tool_name")
            or value.get("arguments") != envelope.get("arguments")
            or value.get("mcp_request_id") != envelope.get("mcp_request_id")
        ):
            raise ValueError("tool approval ack MCP request does not match approval")
        _required_text(value.get("mcp_request_id"), "tool approval ack mcp_request_id")


def _mcp_annotations(value: typing.Any) -> dict[str, bool | None] | None:
    """读取 MCP 工具的严格行为提示字段。"""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("tool.approval_required annotations must be an object")
    allowed = {"destructive_hint", "open_world_hint", "read_only_hint"}
    if set(value) - allowed:
        raise ValueError("tool.approval_required annotations contain unknown fields")
    result: dict[str, bool | None] = {}
    for name in allowed:
        raw = value.get(name)
        if raw is not None and not isinstance(raw, bool):
            raise ValueError(f"tool.approval_required {name} must be boolean or null")
        result[name] = raw
    return result


def _is_json_value(value: typing.Any) -> bool:
    """判断值是否可由 JSON 表示。"""
    if value is None or isinstance(value, (str, bool, int, float)):
        return True
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _is_json_value(item)
            for key, item in value.items()
        )
    return False


def _json_contains(container: typing.Any, candidate: typing.Any) -> bool:
    """判断结构化权限申请是否包含授权内容。"""
    if isinstance(candidate, dict):
        return isinstance(container, dict) and all(
            key in container and _json_contains(container[key], item)
            for key, item in candidate.items()
        )
    if isinstance(candidate, list):
        return isinstance(container, list) and all(
            any(_json_contains(item, wanted) for item in container)
            for wanted in candidate
        )
    return container == candidate


def _approval_started_at(payload: dict[str, typing.Any]) -> int:
    """读取审批信封中的创建时间。"""
    value = _nonnegative_int(payload.get("started_at_ms"))
    if value is None:
        raise ValueError(
            "tool.approval_required started_at_ms is required and must be non-negative"
        )
    return value


def _positive_int(value: typing.Any) -> int | None:
    """读取正整数协议值。"""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value > 0 else None


def _required_positive_int(value: typing.Any, field_name: str) -> int:
    """读取必填正整数协议值。"""
    parsed = _positive_int(value)
    if parsed is None:
        raise ValueError(f"{field_name} must be a positive integer")
    return parsed


def _event_sequence(payload: dict[str, typing.Any]) -> int | None:
    """读取可选事件序号，并拒绝无效的显式值。"""
    if "event_seq" not in payload or payload.get("event_seq") is None:
        return None
    value = payload.get("event_seq")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("event_seq must be a positive integer")
    return value


def _nonnegative_int(value: typing.Any) -> int | None:
    """读取非负整数协议值。"""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


if __name__ == '__main__':
    pass
