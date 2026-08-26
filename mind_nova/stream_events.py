# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import copy
from dataclasses import (
    dataclass,
    field
)
from collections.abc import Mapping
from mind_nova.turn_inputs import TurnInput

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
    """描述符合命令审批契约的客户端决策请求。"""
    call_id: str = ""
    reason: str = ""
    kind: typing.Literal["command", "write_stdin"] = "command"
    approval_id: str = ""
    command: typing.Any = ""
    cwd: str = "."
    proposed_execpolicy_amendment: dict[str, typing.Any] | None = None
    available_decisions: tuple[typing.Any, ...] = ()
    parsed_cmd: tuple[typing.Any, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolCallEvent(ToolEvent):
    """描述服务端下发的原始客户端工具调用。"""


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
    | ToolOutputEvent
    | UnknownStreamEvent
)

_MARKER_EVENT_TYPES = {
    "ping",
    "turn.start",
    "turn.thinking",
    "tool.builtin.call",
    "tool.calls.start",
    "tool.calls.done",
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
        _reject_removed_tool_fields(
            raw,
            event_type,
            extra={"name", "tool", "arguments", "request_id"},
        )
        return ToolApprovalRequiredEvent(
            **common,
            call_id=_required_text(raw.get("call_id"), "tool.approval_required call_id"),
            kind=_approval_kind(raw.get("kind")),
            approval_id=_text(raw.get("approval_id")),
            command=raw.get("command", ""),
            cwd=_text(raw.get("cwd")) or ".",
            reason=_required_text(raw.get("reason"), "tool.approval_required reason"),
            proposed_execpolicy_amendment=_optional_dict(
                raw.get("proposed_execpolicy_amendment")
            ),
            available_decisions=_tuple_or_empty(raw.get("available_decisions")),
            parsed_cmd=_tuple_or_empty(raw.get("parsed_cmd")),
        )
    if event_type == "tool.call":
        _reject_removed_tool_fields(raw, event_type)
        tool_fields = _tool_fields(raw)
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
        _reject_removed_tool_fields(raw, event_type)
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


def _reject_removed_tool_fields(
    payload: dict[str, typing.Any],
    event_type: str,
    *,
    extra: set[str] | None = None,
) -> None:
    """拒绝旧远端工具授权协议字段，避免静默走兼容分支。"""
    removed = {
        "execution",
        "effect",
        "grant",
        "grant_id",
        "grantId",
        "policyVersion",
        "expiresAt",
        "approval",
        "approved",
        "approval_required",
        "approvalRequired",
        "meta",
    }
    if event_type == "tool.call":
        removed.add("request_id")
        removed.add("approval_id")
    removed.update(extra or ())
    present = sorted(field for field in removed if field in payload)
    if present:
        raise ValueError(
            f"{event_type} contains removed protocol fields: {', '.join(present)}"
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


def _approval_kind(value: typing.Any) -> typing.Literal["command", "write_stdin"]:
    """读取命令审批类型。"""
    kind = _text(value) or "command"
    if kind not in {"command", "write_stdin"}:
        raise ValueError("tool.approval_required kind is invalid")
    return typing.cast(typing.Literal["command", "write_stdin"], kind)


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
