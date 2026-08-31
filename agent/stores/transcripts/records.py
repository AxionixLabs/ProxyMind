# -*- coding: utf-8 -*-

import math
import typing
from dataclasses import (
    dataclass,
    field,
)

from agent.ports.transcript import TranscriptActor


@dataclass(frozen=True, slots=True)
class TranscriptEntry:
    """描述会话记录中的单个结构化事件。"""

    timestamp: str
    event: str
    session_id: str
    turn_id: str | None
    actor: TranscriptActor | None
    payload: dict[str, typing.Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: typing.Any) -> "TranscriptEntry":
        """把结构化对象解析为会话事件。"""
        if not isinstance(value, dict):
            raise ValueError("transcript entry must be an object")

        timestamp = _required_text(value.get("timestamp"), "timestamp")
        event = _required_text(value.get("event"), "event")
        session_id = _required_text(value.get("session_id"), "session_id")

        turn_value = value.get("turn_id")
        if turn_value is not None and not isinstance(turn_value, str):
            raise ValueError("transcript turn_id must be a string or null")

        turn_id = str(turn_value or "").strip() or None

        actor_value = value.get("actor")
        if actor_value is not None and (
            not isinstance(actor_value, str)
            or actor_value not in ("user", "assistant", "system", "tool")
        ):
            raise ValueError("transcript actor is invalid")

        payload = value.get("payload", {})
        if not isinstance(payload, dict):
            raise ValueError("transcript payload must be an object")

        return cls(
            timestamp=timestamp,
            event=event,
            session_id=session_id,
            turn_id=turn_id,
            actor=actor_value,
            payload=dict(payload),
        )

    def to_dict(self) -> dict[str, typing.Any]:
        """返回可逐行序列化的事件对象。"""
        return {
            "timestamp": self.timestamp,
            "event": self.event,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "actor": self.actor,
            "payload": _json_value(self.payload),
        }


def _required_text(value: typing.Any, field_name: str) -> str:
    """返回必填文本字段并拒绝空值。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"transcript {field_name} must be a non-empty string")
    return value.strip()


def _json_value(value: typing.Any) -> typing.Any:
    """递归转换为可稳定写入 JSON 的普通值。"""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, dict):
        return {
            str(key): _json_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_value(item) for item in value]

    return str(value)


if __name__ == '__main__':
    pass
