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

EffectClass: typing.TypeAlias = typing.Literal[
    "read_only",
    "idempotent",
    "non_replayable",
]

EffectReplayPolicy: typing.TypeAlias = typing.Literal[
    "safe",
    "provider_idempotent",
    "manual",
]


@dataclass(frozen=True, slots=True)
class ExecutionEffect:
    """描述客户端执行前必须遵守的持久效果约束。"""
    effect_id: str
    fingerprint: str
    effect_class: EffectClass
    replay_policy: EffectReplayPolicy
    provider_idempotency_key: str
    status: str
    dispatch_required: bool
    dispatch_count: int


@dataclass(frozen=True, slots=True)
class WorkspaceCheckpoint:
    """描述本地副作用前必须上传 artifact 的恢复点。"""
    checkpoint_id: str
    required: bool
    workspace: dict[str, typing.Any]
    artifact: dict[str, typing.Any]

    def __post_init__(self) -> None:
        """复制可变字段，保持协议事件不可变。"""
        object.__setattr__(self, "workspace", copy.deepcopy(self.workspace))
        object.__setattr__(self, "artifact", copy.deepcopy(self.artifact))


@dataclass(frozen=True, slots=True, kw_only=True)
class StreamEvent:
    """描述流式协议事件的公共字段。"""
    type: str
    proto: str = ""
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
    effect: ExecutionEffect | None = None
    checkpoint: WorkspaceCheckpoint | None = None

    def __post_init__(self) -> None:
        """复制工具事件中的可变映射字段。"""
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
        """复制公共工具字段和审批载荷。"""
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
        """复制公共工具字段和输出载荷。"""
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
        tool_fields = _tool_fields(raw)

        effect = tool_fields["effect"]
        if effect is None:
            raise ValueError("tool.call execution.effect is required")
        if tool_fields["checkpoint"] is None:
            raise ValueError("tool.call checkpoint is required")
        if (
            effect.effect_class != "non_replayable"
            or effect.replay_policy != "manual"
            or effect.provider_idempotency_key
        ):
            raise ValueError("tool.call requires a manual non-replayable effect")
        if (
            effect.status != "dispatching"
            or not effect.dispatch_required
            or effect.dispatch_count < 1
        ):
            raise ValueError("tool.call effect is not dispatchable")

        return ToolCallEvent(
            **common,
            **tool_fields,
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
    proto   = _text(payload.get("proto"))

    presentation_epoch = (
        _required_positive_int(
            payload.get("presentation_epoch"),
            "presentation_epoch",
        )
        if proto == "mind.chat" and event_type != "ping"
        else (_positive_int(payload.get("presentation_epoch")) or 1)
    )

    return {
        "type": event_type,
        "proto": proto,
        "turn_id": _text(payload.get("turn_id")),
        "event_seq": _event_sequence(payload),
        "round": _positive_int(payload.get("round")),
        "presentation_epoch": presentation_epoch,
        "display": copy.deepcopy(display) if isinstance(display, dict) else None
    }


def _tool_fields(payload: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """提取工具事件共享的字段。"""
    execution = _optional_dict(payload.get("execution"))
    return {
        "name": _text(payload.get("name") or payload.get("tool")),
        "call_id": _text(payload.get("call_id")),
        "arguments": _dict(payload.get("arguments")),
        "meta": _optional_dict(payload.get("meta")),
        "execution": execution,
        "effect": _execution_effect(execution),
        "checkpoint": _workspace_checkpoint(payload.get("checkpoint")),
    }


def _execution_effect(execution: dict[str, typing.Any] | None) -> ExecutionEffect | None:
    """严格解析 execution.effect 并校验分类与重放策略。"""
    if execution is None or "effect" not in execution:
        return None

    value = execution.get("effect")
    if not isinstance(value, dict):
        raise ValueError("execution.effect must be an object")

    effect_id = _required_text(value.get("effect_id"), "execution.effect effect_id")

    fingerprint = _required_text(
        value.get("fingerprint"),
        "execution.effect fingerprint",
    )
    if len(fingerprint) != 64 or any(
        character not in "0123456789abcdef" for character in fingerprint.lower()
    ):
        raise ValueError("execution.effect fingerprint must be a SHA-256 hex digest")

    effect_class = _required_text(value.get("class"), "execution.effect class")

    replay_policy = _required_text(
        value.get("replay_policy"),
        "execution.effect replay_policy",
    )
    if effect_class not in {"read_only", "idempotent", "non_replayable"}:
        raise ValueError("execution.effect class is invalid")
    if replay_policy not in {"safe", "provider_idempotent", "manual"}:
        raise ValueError("execution.effect replay_policy is invalid")
    if effect_class == "non_replayable" and replay_policy != "manual":
        raise ValueError("non-replayable effects require manual replay policy")
    if effect_class == "read_only" and replay_policy != "safe":
        raise ValueError("read-only effects require safe replay policy")

    provider_key = value.get("provider_idempotency_key")
    if not isinstance(provider_key, str):
        raise ValueError("execution.effect provider_idempotency_key must be a string")

    provider_key = provider_key.strip()

    if replay_policy == "provider_idempotent" and not provider_key:
        raise ValueError("provider-idempotent effects require an idempotency key")
    if replay_policy != "provider_idempotent" and provider_key:
        raise ValueError("provider idempotency key requires provider-idempotent replay")

    dispatch_required = value.get("dispatch_required")
    dispatch_count    = value.get("dispatch_count")

    if not isinstance(dispatch_required, bool):
        raise ValueError("execution.effect dispatch_required must be a boolean")

    if (
        isinstance(dispatch_count, bool)
        or not isinstance(dispatch_count, int)
        or dispatch_count < 0
    ):
        raise ValueError("execution.effect dispatch_count must be a non-negative integer")

    return ExecutionEffect(
        effect_id=effect_id,
        fingerprint=fingerprint.lower(),
        effect_class=typing.cast(EffectClass, effect_class),
        replay_policy=typing.cast(EffectReplayPolicy, replay_policy),
        provider_idempotency_key=provider_key,
        status=_required_text(value.get("status"), "execution.effect status"),
        dispatch_required=dispatch_required,
        dispatch_count=dispatch_count,
    )


def _workspace_checkpoint(value: typing.Any) -> WorkspaceCheckpoint | None:
    """严格解析本地副作用执行前的 workspace checkpoint。"""
    if value is None:
        return None

    if not isinstance(value, dict):
        raise ValueError("tool checkpoint must be an object")

    required = value.get("required")
    if not isinstance(required, bool) or not required:
        raise ValueError("tool checkpoint required must be true")

    workspace = value.get("workspace")
    artifact  = value.get("artifact")

    if not isinstance(workspace, dict) or not isinstance(artifact, dict):
        raise ValueError("tool checkpoint workspace and artifact must be objects")

    root = workspace.get("root")
    if not isinstance(root, str) or not root.strip():
        raise ValueError("tool checkpoint workspace.root is required")

    return WorkspaceCheckpoint(
        checkpoint_id=_required_text(
            value.get("checkpoint_id"),
            "tool checkpoint checkpoint_id",
        ),
        required=True,
        workspace=workspace,
        artifact=artifact,
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
