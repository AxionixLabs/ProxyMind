# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import math
import typing
from dataclasses import dataclass

from protocol.schema.json_value import JsonValue

TurnRuntimeStatus: typing.TypeAlias = typing.Literal[
    "queued",
    "running",
    "waiting_tool",
    "waiting_approval",
    "waiting_user",
    "reconciliation_required",
    "finalizing",
    "completed",
    "failed",
    "interrupted",
    "cancelled",
]

TurnCompletedStatus: typing.TypeAlias = typing.Literal[
    "completed",
    "failed",
    "interrupted",
    "cancelled",
]

_TURN_COMPLETED_REQUIRED_FIELDS: typing.Final[frozenset[str]] = frozenset({
    "type",
    "turn_id",
    "status",
    "error",
    "last_event_seq",
    "completed_at",
})
_TURN_COMPLETED_FIELDS: typing.Final[frozenset[str]] = (
    _TURN_COMPLETED_REQUIRED_FIELDS | {"duration_ms"}
)


@dataclass(frozen=True, slots=True)
class TurnCompletedSnapshot:
    """描述与唯一终态事件同构的持久化 wire 快照。"""
    type: typing.Literal["turn.completed"]
    turn_id: str
    status: TurnCompletedStatus
    error: str | None
    last_event_seq: int
    completed_at: float
    duration_ms: int | None = None


def parse_turn_runtime_status(value: JsonValue) -> TurnRuntimeStatus:
    """读取服务端公开的 Durable Turn 运行状态。"""
    if value == "queued":
        return "queued"
    if value == "running":
        return "running"
    if value == "waiting_tool":
        return "waiting_tool"
    if value == "waiting_approval":
        return "waiting_approval"
    if value == "waiting_user":
        return "waiting_user"
    if value == "reconciliation_required":
        return "reconciliation_required"
    if value == "finalizing":
        return "finalizing"
    if value == "completed":
        return "completed"
    if value == "failed":
        return "failed"
    if value == "interrupted":
        return "interrupted"
    if value == "cancelled":
        return "cancelled"
    raise ValueError("turn runtime status is invalid")


def parse_turn_completed_status(value: JsonValue) -> TurnCompletedStatus:
    """读取服务端公开的唯一 Turn 终态。"""
    if value == "completed":
        return "completed"
    if value == "failed":
        return "failed"
    if value == "interrupted":
        return "interrupted"
    if value == "cancelled":
        return "cancelled"
    raise ValueError("turn completed status is invalid")


def is_terminal_turn_status(value: str) -> bool:
    """判断运行状态是否必须携带权威终态快照。"""
    return value in {"completed", "failed", "interrupted", "cancelled"}


def parse_turn_completed_snapshot(
    value: JsonValue,
    *,
    expected_turn_id: str,
    expected_status: str | None = None,
    expected_event_seq: int | None = None,
) -> TurnCompletedSnapshot | None:
    """严格校验并构建 `turn.completed` 的同构终态快照。"""
    if value is None:
        return None
    if (
        not isinstance(value, dict)
        or not _TURN_COMPLETED_REQUIRED_FIELDS.issubset(value)
        or not set(value).issubset(_TURN_COMPLETED_FIELDS)
    ):
        raise ValueError("turn completed snapshot is invalid")

    type_value = value.get("type")
    turn_id = value.get("turn_id")
    status_value = value.get("status")
    error = value.get("error")
    event_seq = value.get("last_event_seq")
    completed_at = value.get("completed_at")
    duration_ms = value.get("duration_ms")

    if (
        type_value != "turn.completed"
        or turn_id != expected_turn_id
        or not isinstance(status_value, str)
        or error is not None and not isinstance(error, str)
        or isinstance(event_seq, bool)
        or not isinstance(event_seq, int)
        or event_seq < 1
        or isinstance(completed_at, bool)
        or not isinstance(completed_at, (int, float))
        or not math.isfinite(float(completed_at))
        or float(completed_at) <= 0
        or duration_ms is not None and (
            isinstance(duration_ms, bool)
            or not isinstance(duration_ms, int)
            or duration_ms < 0
        )
    ):
        raise ValueError("turn completed snapshot is invalid")

    status = parse_turn_completed_status(status_value)
    if expected_status is not None and status != expected_status:
        raise ValueError("turn completed snapshot status does not match response")
    if expected_event_seq is not None and event_seq != expected_event_seq:
        raise ValueError("turn completed snapshot sequence does not match response")

    return TurnCompletedSnapshot(
        type="turn.completed",
        turn_id=expected_turn_id,
        status=status,
        error=error,
        last_event_seq=event_seq,
        completed_at=float(completed_at),
        duration_ms=duration_ms,
    )


if __name__ == '__main__':
    pass
