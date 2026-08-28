# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import copy
import typing
from dataclasses import (
    dataclass,
    field
)
from collections.abc import Mapping
from mind_nova.turn_inputs import TurnInput
from mind_nova.tool_approval import (
    TOOL_APPROVAL_DECISIONS,
    TOOL_APPROVAL_DECISIONS_BY_KIND,
    ToolApprovalDecision,
    ToolApprovalKind,
    ToolApprovalSnapshotStatus
)

TurnDoneStatus: typing.TypeAlias = typing.Literal[
    "completed",
    "incomplete",
    "interrupted",
]

EffectReplay: typing.TypeAlias = typing.Literal["safe", "manual"]

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
class MarkerEvent(StreamEvent):
    """描述不携带业务载荷的流式标记。"""


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
class TurnFailedEvent(TurnTerminalEvent):
    """描述失败的模型轮次。"""
    status: typing.Literal["failed"] = "failed"
    error: str = "unknown error"


@dataclass(frozen=True, slots=True, kw_only=True)
class TurnDoneEvent(TurnTerminalEvent):
    """描述正常、未完整或中断的模型轮次。"""
    status: TurnDoneStatus = "completed"
    reason: str = ""
    can_continue: bool | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class TurnRetryingEvent(StreamEvent):
    """描述当前 Turn 内一次可恢复的 provider 流重试。"""
    round: int
    attempt: int
    max_attempts: int
    retry_in_ms: int
    reason: str = "stream_error"
    supersedes_item_id: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class TurnInputAcceptedEvent(StreamEvent):
    """描述已写入当前逻辑轮次的引导输入。"""
    client_message_id: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class TurnLogicalSettledEvent(StreamEvent):
    """描述逻辑轮次结算后选出的下一轮输入。"""
    next_input: TurnInput | None = None


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
class TextDeltaEvent(StreamEvent):
    """描述 assistant 正文增量。"""
    text: str = ""
    segment_id: str = ""

    @property
    def item_id(self) -> str:
        """返回稳定的 assistant 输出项身份。"""
        return self.segment_id


@dataclass(frozen=True, slots=True, kw_only=True)
class TextDoneEvent(StreamEvent):
    """描述 assistant 正文段完成事件。"""
    segment_id: str = ""
    final_text: str | None = None

    @property
    def item_id(self) -> str:
        """返回稳定的 assistant 输出项身份。"""
        return self.segment_id


@dataclass(frozen=True, slots=True, kw_only=True)
class TextMetaEvent(StreamEvent):
    """描述 assistant 正文段的来源与标注元数据。"""
    segment_id: str = ""
    annotations: tuple[typing.Any, ...] | None = None
    citations: tuple[typing.Any, ...] | None = None
    sources: tuple[typing.Any, ...] | None = None
    source_count: int | None = None

    @property
    def item_id(self) -> str:
        """返回稳定的 assistant 输出项身份。"""
        return self.segment_id


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolBuiltinDoneEvent(StreamEvent):
    """描述内置工具完成后的来源元数据。"""
    sources: tuple[typing.Any, ...] | None = None
    source_count: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolEvent(StreamEvent):
    """描述工具事件的公共调用字段。"""
    name: str = ""
    call_id: str = ""
    arguments: dict[str, typing.Any] = field(default_factory=dict)
    reason: str = ""

    def __post_init__(self) -> None:
        """复制工具事件中的可变映射字段。"""
        object.__setattr__(
            self,
            "arguments",
            copy.deepcopy(dict(self.arguments or {})),
        )
@dataclass(frozen=True, slots=True, kw_only=True)
class ToolApprovalRequiredEvent(StreamEvent):
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
    ready: typing.Literal[True] = True
    timeout_sec: int | None = None


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
    | TurnFailedEvent
    | TurnDoneEvent
    | TurnRetryingEvent
    | TurnInputAcceptedEvent
    | TurnLogicalSettledEvent
    | TurnReconciliationRequiredEvent
    | PresentationSupersededEvent
    | TextDeltaEvent
    | TextDoneEvent
    | TextMetaEvent
    | ToolBuiltinDoneEvent
    | ToolApprovalRequiredEvent
    | ToolCallEvent
    | ToolCallsStartEvent
    | ToolCallsDoneEvent
    | ToolOutputEvent
    | UnknownStreamEvent
)

_MARKER_EVENT_TYPES = {
    "ping",
    "turn.start",
    "turn.thinking",
    "tool.builtin.call",
}


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

    if event_type != "ping" and (
        "seq" in raw or "replace_current_response" in raw
    ):
        raise ValueError("stream event contains a removed protocol field")

    common = _common_fields(raw, event_type)

    if event_type == "tool.calls.start":
        return ToolCallsStartEvent(
            **common,
            **_tool_calls_boundary_fields(raw, "tool.calls.start"),
        )
    if event_type == "tool.calls.done":
        return ToolCallsDoneEvent(
            **common,
            **_tool_calls_boundary_fields(raw, "tool.calls.done"),
        )

    if event_type in _MARKER_EVENT_TYPES:
        return MarkerEvent(**common)
    if event_type == "turn.failed":
        return TurnFailedEvent(
            **common,
            **_terminal_fields(raw),
            error=_error_text(raw.get("error")),
        )
    if event_type == "turn.done":
        return TurnDoneEvent(
            **common,
            **_terminal_fields(raw),
            status=_turn_done_status(raw.get("status")),
            reason=_text(raw.get("reason")),
            can_continue=_optional_bool(raw.get("can_continue")),
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
        )
    if event_type == "turn.input.accepted":
        return TurnInputAcceptedEvent(
            **common,
            client_message_id=_text(raw.get("client_message_id")),
        )
    if event_type == "turn.logical_settled":
        return TurnLogicalSettledEvent(
            **common,
            next_input=_turn_input_or_none(raw.get("next_input")),
        )
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
    if event_type == "text.delta":
        return TextDeltaEvent(
            **common,
            text=str(raw.get("text") or ""),
            segment_id=_required_text(
                raw.get("segment_id"),
                "text.delta segment_id",
            ),
        )
    if event_type == "text.done":
        return TextDoneEvent(
            **common,
            segment_id=_required_text(
                raw.get("segment_id"),
                "text.done segment_id",
            ),
            final_text=_optional_content_text(raw.get("final_text")),
        )
    if event_type == "text.meta":
        return TextMetaEvent(
            **common,
            segment_id=_required_text(
                raw.get("segment_id"),
                "text.meta segment_id",
            ),
            annotations=_tuple_or_none(raw.get("annotations")),
            citations=_tuple_or_none(raw.get("citations")),
            sources=_tuple_or_none(raw.get("sources")),
            source_count=_nonnegative_int(raw.get("source_count")),
        )
    if event_type == "tool.builtin.done":
        return ToolBuiltinDoneEvent(
            **common,
            sources=_tuple_or_none(raw.get("sources")),
            source_count=_nonnegative_int(raw.get("source_count")),
        )
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
        present_removed = sorted(field for field in removed_fields if field in raw)
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
            available_decisions.append(typing.cast(ToolApprovalDecision, decision))
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
        tool_fields = _tool_fields(raw)
        if not tool_fields["name"]:
            raise ValueError("tool.call name is required")
        if not tool_fields["call_id"]:
            raise ValueError("tool.call call_id is required")
        if tool_fields["name"] in {
            "shell_command",
            "exec_command",
            "write_stdin",
        } and not tool_fields["reason"]:
            raise ValueError("tool.call shell reason is required")
        return ToolCallEvent(
            **common,
            **tool_fields,
        )

    if event_type == "tool.output":
        return ToolOutputEvent(
            **common,
            **_tool_fields(raw),
            payload=raw,
        )

    return UnknownStreamEvent(**common, payload=raw)


def _common_fields(
    payload: dict[str, typing.Any],
    event_type: str
) -> dict[str, typing.Any]:
    """提取所有流式事件共享的字段。"""
    display = payload.get("display")
    if event_type == "ping":
        proto = _text(payload.get("proto"))

        cid     = _text(payload.get("cid"))
        sid     = _text(payload.get("sid"))
        turn_id = _text(payload.get("turn_id"))

        event_seq = _event_sequence(payload)

        presentation_epoch = _positive_int(
            payload.get("presentation_epoch")
        ) or 1

    else:
        proto = _required_text(payload.get("proto"), "proto")
        if proto != "mind.chat":
            raise ValueError("stream event proto must be mind.chat")

        cid     = _required_text(payload.get("cid"), "cid")
        sid     = _required_text(payload.get("sid"), "sid")
        turn_id = _required_text(payload.get("turn_id"), "turn_id")

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


def _tool_fields(payload: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """提取工具事件共享的调用字段。"""
    return {
        "name": _text(payload.get("name") or payload.get("tool")),
        "call_id": _text(payload.get("call_id")),
        "arguments": _dict(payload.get("arguments")),
        "reason": _text(payload.get("reason")),
    }


def _tool_calls_boundary_fields(
    payload: dict[str, typing.Any],
    event_type: str,
) -> dict[str, typing.Any]:
    """读取并校验客户端工具批次边界字段。"""
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
    if payload.get("ready") is not True:
        raise ValueError(f"{event_type} ready must be true")
    timeout_sec = payload.get("timeout_sec")
    if timeout_sec is not None:
        timeout_sec = _required_positive_int(
            timeout_sec,
            f"{event_type} timeout_sec",
        )
    return {
        "batch_id": batch_id,
        "call_ids": call_ids,
        "count": count,
        "ready": True,
        "timeout_sec": timeout_sec,
    }


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


def _optional_bool(value: typing.Any) -> bool | None:
    """读取可选布尔协议值。"""
    return value if isinstance(value, bool) else None


def _turn_done_status(value: typing.Any) -> TurnDoneStatus:
    """读取轮次完成事件的受支持状态。"""
    status = _text(value) or "completed"
    if status == "completed":
        return "completed"
    if status == "incomplete":
        return "incomplete"
    if status == "interrupted":
        return "interrupted"

    raise ValueError(f"unsupported turn.done status: {status}")


def _error_text(value: typing.Any) -> str:
    """读取失败终态中优先展示的错误说明。"""
    if isinstance(value, Mapping):
        return _text(value.get("message")) or "unknown error"
    return _text(value) or "unknown error"


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


def _turn_input_or_none(value: typing.Any) -> TurnInput | None:
    """读取可选的下一逻辑轮次输入。"""
    if not isinstance(value, Mapping):
        return None
    try:
        return TurnInput.from_mapping(value)
    except (TypeError, ValueError):
        return None


def _tuple_or_none(value: typing.Any) -> tuple[typing.Any, ...] | None:
    """复制可选列表协议值。"""
    return tuple(copy.deepcopy(value)) if isinstance(value, list) else None


def _tuple_or_empty(value: typing.Any) -> tuple[typing.Any, ...]:
    """读取可选列表并在缺省时返回空元组。"""
    return tuple(copy.deepcopy(value)) if isinstance(value, list) else ()


def _approval_kind(
    value: typing.Any,
) -> ToolApprovalKind:
    """读取直接审批请求的操作类型。"""
    kind = _required_text(value, "tool.approval_required kind")
    if kind not in TOOL_APPROVAL_DECISIONS_BY_KIND:
        raise ValueError("tool.approval_required kind is invalid")
    return typing.cast(ToolApprovalKind, kind)


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
    if value not in {
        "use_default",
        "require_escalated",
        "with_additional_permissions",
    }:
        raise ValueError("tool.approval_required sandbox_permissions is invalid")
    return typing.cast(str, value)


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
