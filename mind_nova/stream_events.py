# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import copy
from dataclasses import (
    dataclass,
    field
)
from collections.abc import Mapping


@dataclass(frozen=True, slots=True, kw_only=True)
class StreamEvent:
    """描述流式协议事件的公共字段。"""
    type: str
    proto: str = ""
    round: int | None = None
    display: dict[str, typing.Any] | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class MarkerEvent(StreamEvent):
    """描述不携带业务载荷的流式标记。"""


@dataclass(frozen=True, slots=True, kw_only=True)
class TurnFailedEvent(StreamEvent):
    """描述失败的模型轮次。"""
    error: str = "unknown error"


@dataclass(frozen=True, slots=True, kw_only=True)
class TurnDoneEvent(StreamEvent):
    """描述已完成的模型轮次。"""
    usage: dict[str, typing.Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "usage", copy.deepcopy(dict(self.usage or {})))


@dataclass(frozen=True, slots=True, kw_only=True)
class TextDeltaEvent(StreamEvent):
    """描述 assistant 正文增量。"""
    text: str = ""
    segment_id: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class TextDoneEvent(StreamEvent):
    """描述 assistant 正文段完成事件。"""
    segment_id: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class TextMetaEvent(StreamEvent):
    """描述 assistant 正文段的来源与标注元数据。"""
    segment_id: str = ""
    annotations: tuple[typing.Any, ...] | None = None
    citations: tuple[typing.Any, ...] | None = None
    sources: tuple[typing.Any, ...] | None = None
    source_count: int | None = None


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
    meta: dict[str, typing.Any] | None = None
    execution: dict[str, typing.Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "arguments",
            copy.deepcopy(dict(self.arguments or {})),
        )
        if self.meta is not None:
            object.__setattr__(self, "meta", copy.deepcopy(dict(self.meta)))
        if self.execution is not None:
            object.__setattr__(self, "execution", copy.deepcopy(dict(self.execution)))


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolApprovalRequiredEvent(ToolEvent):
    """描述需要客户端决策的工具审批事件。"""

    approval: dict[str, typing.Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        ToolEvent.__post_init__(self)
        object.__setattr__(
            self,
            "approval",
            copy.deepcopy(dict(self.approval or {})),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolCallEvent(ToolEvent):
    """描述服务端下发的客户端工具调用。"""
    approval_id: str = ""
    approved: bool = False
    approval_required: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolOutputEvent(ToolEvent):
    """描述服务端工具输出回灌事件。"""
    payload: dict[str, typing.Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        ToolEvent.__post_init__(self)
        object.__setattr__(self, "payload", copy.deepcopy(dict(self.payload or {})))


@dataclass(frozen=True, slots=True, kw_only=True)
class UnknownStreamEvent(StreamEvent):
    """描述尚未建模的扩展事件。"""
    payload: dict[str, typing.Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", copy.deepcopy(dict(self.payload or {})))


ChatStreamEvent: typing.TypeAlias = (
    MarkerEvent
    | TurnFailedEvent
    | TurnDoneEvent
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


def parse_stream_event(payload: Mapping[str, typing.Any]) -> ChatStreamEvent:
    """把流式协议对象解析为稳定事件类型。"""
    if not isinstance(payload, Mapping):
        raise TypeError("stream event must be an object")

    raw = dict(payload)

    event_type = str(raw.get("type") or "").strip()
    if not event_type:
        raise ValueError("stream event type is required")

    common = _common_fields(raw, event_type)

    if event_type in _MARKER_EVENT_TYPES:
        return MarkerEvent(**common)
    if event_type == "turn.failed":
        return TurnFailedEvent(
            **common,
            error=str(raw.get("error") or "unknown error"),
        )
    if event_type == "turn.done":
        return TurnDoneEvent(
            **common,
            usage=_dict(raw.get("usage")),
        )
    if event_type == "text.delta":
        return TextDeltaEvent(
            **common,
            text=str(raw.get("text") or ""),
            segment_id=_text(raw.get("segment_id")),
        )
    if event_type == "text.done":
        return TextDoneEvent(
            **common,
            segment_id=_text(raw.get("segment_id")),
        )
    if event_type == "text.meta":
        return TextMetaEvent(
            **common,
            segment_id=_text(raw.get("segment_id")),
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
        return ToolApprovalRequiredEvent(
            **common,
            **_tool_fields(raw),
            approval=_dict(raw.get("approval")),
        )
    if event_type == "tool.call":
        return ToolCallEvent(
            **common,
            **_tool_fields(raw),
            approval_id=_text(raw.get("approval_id")),
            approved=_truthy(raw.get("approved")),
            approval_required=_approval_required(raw),
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

    return {
        "type"    : event_type,
        "proto"   : _text(payload.get("proto")),
        "round"   : _positive_int(payload.get("round")),
        "display" : copy.deepcopy(display) if isinstance(display, dict) else None
    }


def _tool_fields(payload: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """提取工具事件共享的字段。"""
    return {
        "name"      : _text(payload.get("name") or payload.get("tool")),
        "call_id"   : _text(payload.get("call_id")),
        "arguments" : _dict(payload.get("arguments")),
        "meta"      : _optional_dict(payload.get("meta")),
        "execution" : _optional_dict(payload.get("execution"))
    }


def _approval_required(payload: dict[str, typing.Any]) -> bool:
    """读取工具事件顶层的审批要求。"""
    return any(
        _truthy(value)
        for value in (
            payload.get("approvalRequired"),
            payload.get("approval_required"),
        )
    )


def _truthy(value: typing.Any) -> bool:
    """把协议中的布尔兼容值转换为布尔值。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "required"}
    if isinstance(value, (int, float)):
        return bool(value)
    return False


def _text(value: typing.Any) -> str:
    """把可选协议值转换为文本。"""
    return str(value or "").strip()


def _dict(value: typing.Any) -> dict[str, typing.Any]:
    """复制字典协议值。"""
    return copy.deepcopy(value) if isinstance(value, dict) else {}


def _optional_dict(value: typing.Any) -> dict[str, typing.Any] | None:
    """复制可选字典协议值。"""
    return copy.deepcopy(value) if isinstance(value, dict) else None


def _tuple_or_none(value: typing.Any) -> tuple[typing.Any, ...] | None:
    """复制可选列表协议值。"""
    return tuple(copy.deepcopy(value)) if isinstance(value, list) else None


def _positive_int(value: typing.Any) -> int | None:
    """读取正整数协议值。"""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value > 0 else None


def _nonnegative_int(value: typing.Any) -> int | None:
    """读取非负整数协议值。"""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


if __name__ == '__main__':
    pass
