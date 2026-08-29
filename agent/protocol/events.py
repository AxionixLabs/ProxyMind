# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import uuid
import typing
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

RunEventKind = typing.Literal[
    "run_queued",
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
