# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from collections.abc import Mapping
from dataclasses import (
    dataclass,
    field,
)

from .commands import SubmitTurnCommand
from .model import ModelStreamRequest

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

LocalDurableQueueStatus: typing.TypeAlias = typing.Literal[
    "adding",
    "queued",
    "starting",
    "started",
    "settled",
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


@dataclass(frozen=True, slots=True)
class LocalDurableQueueSnapshot:
    """保存服务端队列命令恢复所需的本地冻结执行事实。

    服务端仍拥有队列顺序和远端状态。本快照只保证响应丢失或进程重启后能够复用
    原幂等身份，并在 queue.start 成功后按入队时权限、环境和输入观察既有 Turn。
    """

    submission_id: str
    client_message_id: str
    add_request_id: str
    command: SubmitTurnCommand
    request: ModelStreamRequest
    status: LocalDurableQueueStatus
    revision: int
    queue_version: int
    start_request_id: str | None
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:
        """校验本地身份与冻结请求坐标完全一致。"""
        for field_name in (
            "submission_id",
            "client_message_id",
            "add_request_id",
            "created_at",
            "updated_at",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"local durable queue {field_name} is required")
            object.__setattr__(self, field_name, value.strip())
        if self.status not in {
            "adding",
            "queued",
            "starting",
            "started",
            "settled",
            "deleted",
        }:
            raise ValueError("local durable queue status is invalid")
        if (
            isinstance(self.revision, bool)
            or not isinstance(self.revision, int)
            or self.revision < 1
        ):
            raise ValueError("local durable queue revision must be positive")
        if (
            isinstance(self.queue_version, bool)
            or not isinstance(self.queue_version, int)
            or self.queue_version < 0
        ):
            raise ValueError(
                "local durable queue version must be non-negative"
            )
        start_request_id = str(self.start_request_id or "").strip() or None
        if self.status in {"starting", "started"} and start_request_id is None:
            raise ValueError("started local durable queue item requires request id")
        object.__setattr__(self, "start_request_id", start_request_id)
        if (
            self.request.cid != _remote_coordinate(self.command, "cid")
            or self.request.sid != _remote_coordinate(self.command, "sid")
            or self.request.turn_id != _remote_coordinate(self.command, "turn_id")
        ):
            raise ValueError(
                "local durable queue command and request coordinates differ"
            )


def _remote_coordinate(command: SubmitTurnCommand, field_name: str) -> str:
    """从本地 Command 读取一个严格远端坐标。"""
    remote_turn = command.trace_context.get("remote_turn")
    if not isinstance(remote_turn, Mapping):
        raise ValueError("local durable queue command requires remote turn")
    value = remote_turn.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"local durable queue command requires remote {field_name}"
        )
    return value.strip()


if __name__ == '__main__':
    pass
