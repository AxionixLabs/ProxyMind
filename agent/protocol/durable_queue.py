# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from collections.abc import Mapping
from dataclasses import (
    dataclass,
    field,
)

from .json_value import (
    JsonValue,
    ThawedJsonValue,
    freeze_json,
    thaw_object,
)

DurableQueueItemStatus: typing.TypeAlias = typing.Literal[
    "queued",
    "started",
    "deleted",
]


@dataclass(frozen=True, slots=True)
class DurableQueueInput:
    """描述不含凭据和身份的持久队列输入投影。"""

    text: str
    attachments: tuple[Mapping[str, JsonValue], ...] = ()
    extras: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """冻结服务端返回的输入投影。"""
        if not isinstance(self.text, str):
            raise TypeError("durable queue input text must be a string")
        frozen_attachments: list[Mapping[str, JsonValue]] = []
        for attachment in self.attachments:
            if not isinstance(attachment, Mapping):
                raise TypeError("durable queue attachment must be an object")
            frozen = freeze_json(
                dict(attachment),
                field_name="durable queue attachments",
            )
            if not isinstance(frozen, Mapping):
                raise TypeError("durable queue attachment must be an object")
            frozen_attachments.append(frozen)
        frozen_extras = freeze_json(
            dict(self.extras),
            field_name="durable queue extras",
        )
        if not isinstance(frozen_extras, Mapping):
            raise TypeError("durable queue extras must be an object")
        object.__setattr__(self, "attachments", tuple(frozen_attachments))
        object.__setattr__(self, "extras", frozen_extras)

    def attachment_values(self) -> list[dict[str, ThawedJsonValue]]:
        """返回独立的附件投影。"""
        return [
            thaw_object(item, field_name="durable queue attachments")
            for item in self.attachments
        ]

    def extras_value(self) -> dict[str, ThawedJsonValue]:
        """返回独立的扩展字段投影。"""
        return thaw_object(self.extras, field_name="durable queue extras")


@dataclass(frozen=True, slots=True)
class DurableQueueItem:
    """描述服务端拥有的持久队列项。"""

    queue_seq: int
    cid: str
    sid: str
    submission_id: str
    client_message_id: str
    turn_id: str
    position: int | None
    status: DurableQueueItemStatus
    input: DurableQueueInput
    created_at: float
    updated_at: float
    started_at: float | None = None
    deleted_at: float | None = None


@dataclass(frozen=True, slots=True)
class DurableQueueSnapshot:
    """描述一个 Session 的服务端权威持久队列。"""

    cid: str
    sid: str
    queue_version: int
    items: tuple[DurableQueueItem, ...]


@dataclass(frozen=True, slots=True)
class DurableQueueMutationReceipt:
    """描述 add、update 或 delete 的服务端持久回执。"""

    request_id: str
    queue_version: int
    item: DurableQueueItem
    status: typing.Literal["accepted"] = "accepted"


@dataclass(frozen=True, slots=True)
class DurableQueueReorderReceipt:
    """描述服务端接受完整 FIFO 顺序后的持久回执。"""

    request_id: str
    queue_version: int
    submission_ids: tuple[str, ...]
    status: typing.Literal["accepted"] = "accepted"


@dataclass(frozen=True, slots=True)
class DurableQueueStartReceipt:
    """描述 Queue item 被原子绑定到新 Turn 的持久回执。"""

    request_id: str
    queue_version: int
    submission_id: str
    turn_id: str
    status: typing.Literal["started"] = "started"


if __name__ == '__main__':
    pass
