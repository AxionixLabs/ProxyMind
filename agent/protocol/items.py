# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from collections.abc import Mapping
from dataclasses import (
    dataclass,
    field
)

from .json_value import (
    JsonValue,
    ThawedJsonValue,
    freeze_json,
    thaw_object,
)


AssistantTextPhase: typing.TypeAlias = typing.Literal[
    "commentary",
    "final_answer",
]


@dataclass(frozen=True, slots=True)
class CanonicalItem:
    """描述 Protocol Client 可交给任意前端的不可变 Item 快照。

    快照只表达已归约的协议事实，不包含 TUI 组件、Transcript 写入状态或本地工具
    句柄。实现方必须保留展示代次和 provider attempt，使被替换内容仍可供审计。
    """

    cid: str
    sid: str
    turn_id: str
    item_id: str
    item_kind: str
    item_status: str
    presentation_epoch: int
    round_no: int
    attempt: int
    first_event_seq: int
    last_event_seq: int
    last_event_type: str
    phase: AssistantTextPhase | None = None
    payload: Mapping[str, JsonValue] = field(default_factory=dict)
    superseded: bool = False
    superseded_by_epoch: int | None = None
    superseded_by_attempt: int | None = None

    def __post_init__(self) -> None:
        """校验身份、序号和替代标记并冻结展示载荷。"""
        for field_name in (
                "cid",
                "sid",
                "turn_id",
                "item_id",
                "item_kind",
                "item_status",
                "last_event_type",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"canonical item {field_name} is required")
            object.__setattr__(self, field_name, value.strip())
        for field_name in (
                "presentation_epoch",
                "round_no",
                "attempt",
                "first_event_seq",
                "last_event_seq",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"canonical item {field_name} must be positive")
        if self.last_event_seq < self.first_event_seq:
            raise ValueError("canonical item event sequence cannot move backwards")
        if self.phase is not None and (
            self.item_kind != "text"
            or self.phase not in {"commentary", "final_answer"}
        ):
            raise ValueError("canonical assistant text phase is invalid")
        if not isinstance(self.payload, Mapping):
            raise TypeError("canonical item payload must be an object")
        if not isinstance(self.superseded, bool):
            raise TypeError("canonical item superseded must be boolean")
        for field_name in ("superseded_by_epoch", "superseded_by_attempt"):
            value = getattr(self, field_name)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 1
            ):
                raise ValueError(f"canonical item {field_name} must be positive")
        if not self.superseded and (
            self.superseded_by_epoch is not None
            or self.superseded_by_attempt is not None
        ):
            raise ValueError("active canonical item cannot have a replacement")
        frozen = freeze_json(
            dict(self.payload),
            field_name="canonical item payload",
        )
        if not isinstance(frozen, Mapping):
            raise TypeError("canonical item payload must be an object")
        object.__setattr__(self, "payload", frozen)

    def payload_value(self) -> dict[str, ThawedJsonValue]:
        """返回供前端独立消费的普通 JSON 对象。"""
        return thaw_object(self.payload, field_name="canonical item payload")


if __name__ == '__main__':
    pass
