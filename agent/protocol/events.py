# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import (
    datetime,
    timezone
)

from .json_value import (
    JsonValue,
    freeze_json,
    thaw_json
)


@typing.runtime_checkable
class ModelEvent(typing.Protocol):
    """定义模型事件跨 capability 边界共享的最小坐标契约。

    传输 adapter 可以使用自己的具体事件类；实现方必须提供稳定事件类型、线上
    会话坐标和展示代次，runtime 不得依赖具体网络传输包。
    """

    type: str
    proto: str
    cid: str
    sid: str
    turn_id: str
    event_seq: int | None
    presentation_epoch: int


def validate_model_event(event: ModelEvent) -> ModelEvent:
    """校验模型事件跨 capability 边界的公共协议字段。"""
    if not isinstance(event, ModelEvent):
        raise TypeError("model transport returned an invalid event object")

    if not isinstance(event.type, str) or not event.type.strip():
        raise ValueError("model event type is required")
    for field_name, value in (
            ("proto", event.proto),
            ("cid", event.cid),
            ("sid", event.sid),
            ("turn_id", event.turn_id),
    ):
        if not isinstance(value, str):
            raise TypeError(f"model event {field_name} must be a string")

    if event.type in {"ping", "stream.gap"}:
        if event.type == "stream.gap":
            for field_name, value in (
                    ("cid", event.cid),
                    ("sid", event.sid),
                    ("turn_id", event.turn_id),
            ):
                if not value.strip():
                    raise ValueError(f"model event {field_name} is required")
            if event.event_seq is not None:
                raise ValueError("model event stream.gap must not have event_seq")
        if event.event_seq is not None and (
            isinstance(event.event_seq, bool)
            or not isinstance(event.event_seq, int)
            or event.event_seq <= 0
        ):
            raise ValueError(f"model event {event.type} event_seq must be positive")
        if (
            isinstance(event.presentation_epoch, bool)
            or not isinstance(event.presentation_epoch, int)
            or event.presentation_epoch < 1
        ):
            raise ValueError("model event presentation_epoch must be positive")
        return event

    if event.proto != "mind.chat":
        raise ValueError("model event proto must be mind.chat")
    for field_name, value in (
            ("cid", event.cid),
            ("sid", event.sid),
            ("turn_id", event.turn_id),
    ):
        if not value.strip():
            raise ValueError(f"model event {field_name} is required")
    if (
        isinstance(event.event_seq, bool)
        or not isinstance(event.event_seq, int)
        or event.event_seq < 1
    ):
        raise ValueError("model event event_seq must be positive")
    if (
        isinstance(event.presentation_epoch, bool)
        or not isinstance(event.presentation_epoch, int)
        or event.presentation_epoch < 1
    ):
        raise ValueError("model event presentation_epoch must be positive")
    return event


RunEventKind = typing.Literal[
    "run_queued",
    "run_redispatch_queued",
    "run_started",
    "run_waiting_approval",
    "run_waiting_effect",
    "run_paused",
    "run_completed",
    "run_failed",
    "run_incomplete",
    "run_interrupted",
    "run_cancelled",
    "run_reconciliation_required",
]


@dataclass(frozen=True, slots=True)
class RunEvent:
    """描述同一个本地 Run 中单调递增的状态事实。"""

    event_id: str
    sequence: int
    session_id: str
    run_id: str
    kind: RunEventKind
    payload: Mapping[str, JsonValue]
    causation_id: str
    occurred_at: str

    def __post_init__(self) -> None:
        """校验事件坐标并冻结事件载荷。"""
        for field_name in (
                "event_id",
                "session_id",
                "run_id",
                "causation_id",
                "occurred_at",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} is required")
            object.__setattr__(self, field_name, value.strip())
        if (
            isinstance(self.sequence, bool)
            or not isinstance(self.sequence, int)
            or self.sequence < 1
        ):
            raise ValueError("sequence must be a positive integer")
        if not isinstance(self.payload, Mapping):
            raise TypeError("payload must be an object")
        frozen = freeze_json(dict(self.payload), field_name="payload")
        if not isinstance(frozen, Mapping):
            raise TypeError("payload must be an object")
        object.__setattr__(self, "payload", frozen)

    @classmethod
    def create(
        cls,
        *,
        sequence: int,
        session_id: str,
        run_id: str,
        kind: RunEventKind,
        payload: Mapping[str, typing.Any],
        causation_id: str,
    ) -> "RunEvent":
        """创建包含稳定坐标和 UTC 时间的事件。"""
        return cls(
            event_id=f"event_{uuid.uuid4().hex}",
            sequence=sequence,
            session_id=session_id,
            run_id=run_id,
            kind=kind,
            payload=payload,
            causation_id=causation_id,
            occurred_at=datetime.now(timezone.utc).isoformat(),
        )

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, typing.Any],
    ) -> "RunEvent":
        """从持久化协议字典还原并重新校验 Run 事件。"""
        if not isinstance(value, Mapping):
            raise TypeError("run event must be an object")
        return cls(
            event_id=value.get("event_id"),
            sequence=value.get("sequence"),
            session_id=value.get("session_id"),
            run_id=value.get("run_id"),
            kind=value.get("kind"),
            payload=value.get("payload"),
            causation_id=value.get("causation_id"),
            occurred_at=value.get("occurred_at"),
        )

    def to_dict(self) -> dict[str, typing.Any]:
        """返回适合 adapter 投影和持久化的普通字典。"""
        return {
            "event_id": self.event_id,
            "sequence": self.sequence,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "kind": self.kind,
            "payload": thaw_json(self.payload),
            "causation_id": self.causation_id,
            "occurred_at": self.occurred_at,
        }


if __name__ == '__main__':
    pass
