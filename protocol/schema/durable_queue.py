# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import copy
import math
import typing
from collections.abc import Mapping
from dataclasses import dataclass

from protocol.schema.identifiers import (
    normalize_submission_id,
    normalize_turn_id,
)
from protocol.schema.json_value import JsonValue

QueueItemStatus: typing.TypeAlias = typing.Literal[
    "queued",
    "started",
    "deleted",
]


class DurableQueueResponseError(ValueError):
    """描述服务端持久队列响应不符合正式契约。"""


@dataclass(frozen=True, slots=True)
class QueueInputSnapshot:
    """描述不含凭据和消息身份的持久队列输入快照。"""

    text: str
    attachments: tuple[Mapping[str, JsonValue], ...]
    extras: Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class DurableQueueItem:
    """描述服务端拥有的单个持久队列项。"""

    queue_seq: int
    cid: str
    sid: str
    submission_id: str
    client_message_id: str
    turn_id: str
    position: int | None
    status: QueueItemStatus
    input: QueueInputSnapshot
    created_at: float
    updated_at: float
    started_at: float | None
    deleted_at: float | None


@dataclass(frozen=True, slots=True)
class QueueSnapshot:
    """描述某个 Session 的服务端权威持久队列快照。"""

    cid: str
    sid: str
    queue_version: int
    items: tuple[DurableQueueItem, ...]


@dataclass(frozen=True, slots=True)
class QueueMutationResponse:
    """描述 add、update 或 delete 返回的持久事实。"""

    request_id: str
    queue_version: int
    item: DurableQueueItem
    status: typing.Literal["accepted"] = "accepted"


@dataclass(frozen=True, slots=True)
class QueueReorderResponse:
    """描述完整队列顺序被原子替换后的持久事实。"""

    request_id: str
    queue_version: int
    submission_ids: tuple[str, ...]
    status: typing.Literal["accepted"] = "accepted"


@dataclass(frozen=True, slots=True)
class QueueStartResponse:
    """描述队首提交被原子转换为 Turn 的持久事实。"""

    request_id: str
    queue_version: int
    submission_id: str
    turn_id: str
    status: typing.Literal["started"] = "started"


def parse_queue_snapshot(
    body: JsonValue,
    *,
    expected_cid: str,
    expected_sid: str,
) -> QueueSnapshot:
    """校验并构建服务端持久队列快照。"""
    data = _response_data(body)
    _require_keys(data, {"cid", "sid", "queue_version", "items"}, "queue snapshot")
    items_value = data.get("items")
    queue_version = _non_negative_int(data.get("queue_version"), "queue_version")
    if (
        data.get("cid") != expected_cid
        or data.get("sid") != expected_sid
        or not isinstance(items_value, list)
    ):
        raise DurableQueueResponseError("queue snapshot does not match session")

    items = tuple(
        _queue_item(
            value,
            expected_cid=expected_cid,
            expected_sid=expected_sid,
        )
        for value in items_value
    )
    if any(item.status != "queued" for item in items):
        raise DurableQueueResponseError("queue snapshot contains a non-queued item")
    if tuple(item.position for item in items) != tuple(range(1, len(items) + 1)):
        raise DurableQueueResponseError("queue snapshot positions are not contiguous")
    if len({item.submission_id for item in items}) != len(items):
        raise DurableQueueResponseError("queue snapshot contains duplicate submissions")
    if len({item.client_message_id for item in items}) != len(items):
        raise DurableQueueResponseError("queue snapshot contains duplicate messages")
    return QueueSnapshot(
        cid=expected_cid,
        sid=expected_sid,
        queue_version=queue_version,
        items=items,
    )


def parse_queue_mutation(
    body: JsonValue,
    *,
    expected_cid: str,
    expected_sid: str,
    expected_request_id: str,
    expected_submission_id: str,
) -> QueueMutationResponse:
    """校验 add、update 或 delete 的权威回执。"""
    data = _response_data(body)
    _require_keys(
        data,
        {"request_id", "status", "queue_version", "item"},
        "queue mutation",
    )
    if (
        data.get("status") != "accepted"
        or data.get("request_id") != expected_request_id
    ):
        raise DurableQueueResponseError("queue mutation does not match request")
    item = _queue_item(
        data.get("item"),
        expected_cid=expected_cid,
        expected_sid=expected_sid,
    )
    if item.submission_id != expected_submission_id:
        raise DurableQueueResponseError("queue mutation does not match submission")
    return QueueMutationResponse(
        request_id=expected_request_id,
        queue_version=_positive_int(data.get("queue_version"), "queue_version"),
        item=item,
    )


def parse_queue_reorder(
    body: JsonValue,
    *,
    expected_request_id: str,
    expected_submission_ids: tuple[str, ...],
) -> QueueReorderResponse:
    """校验原子重排回执与提交的完整顺序一致。"""
    data = _response_data(body)
    _require_keys(
        data,
        {"request_id", "status", "queue_version", "submission_ids"},
        "queue reorder",
    )
    values = data.get("submission_ids")
    if not isinstance(values, list):
        raise DurableQueueResponseError("queue reorder returned invalid submissions")
    submission_ids = tuple(_submission_id(value) for value in values)
    if (
        data.get("status") != "accepted"
        or data.get("request_id") != expected_request_id
        or submission_ids != expected_submission_ids
    ):
        raise DurableQueueResponseError("queue reorder does not match request")
    return QueueReorderResponse(
        request_id=expected_request_id,
        queue_version=_positive_int(data.get("queue_version"), "queue_version"),
        submission_ids=submission_ids,
    )


def parse_queue_start(
    body: JsonValue,
    *,
    expected_request_id: str,
    expected_submission_id: str,
) -> QueueStartResponse:
    """校验 Queue item 到 Turn 的原子转换回执。"""
    data = _response_data(body)
    _require_keys(
        data,
        {"request_id", "status", "queue_version", "submission_id", "turn_id"},
        "queue start",
    )
    if (
        data.get("status") != "started"
        or data.get("request_id") != expected_request_id
        or data.get("submission_id") != expected_submission_id
    ):
        raise DurableQueueResponseError("queue start does not match request")
    turn_id_value = data.get("turn_id")
    if not isinstance(turn_id_value, str):
        raise DurableQueueResponseError("queue start returned an invalid turn_id")
    try:
        turn_id = normalize_turn_id(turn_id_value)
    except ValueError as error:
        raise DurableQueueResponseError(str(error)) from error
    return QueueStartResponse(
        request_id=expected_request_id,
        queue_version=_positive_int(data.get("queue_version"), "queue_version"),
        submission_id=expected_submission_id,
        turn_id=turn_id,
    )


def _response_data(body: JsonValue) -> dict[str, JsonValue]:
    """读取 Queue API 的统一 data 对象。"""
    if not isinstance(body, dict) or body.get("ok") is not True:
        raise DurableQueueResponseError("queue returned an invalid response")
    _require_keys(body, {"ok", "data"}, "queue response")
    data = body.get("data")
    if not isinstance(data, dict):
        raise DurableQueueResponseError("queue returned an invalid response")
    return data


def _queue_item(
    value: JsonValue,
    *,
    expected_cid: str,
    expected_sid: str,
) -> DurableQueueItem:
    """校验单个无凭据 Queue item。"""
    if not isinstance(value, dict):
        raise DurableQueueResponseError("queue item is invalid")
    _require_keys(
        value,
        {
            "queue_seq",
            "cid",
            "sid",
            "submission_id",
            "client_message_id",
            "turn_id",
            "position",
            "status",
            "input",
            "created_at",
            "updated_at",
            "started_at",
            "deleted_at",
        },
        "queue item",
    )
    status = _item_status(value.get("status"))
    position_value = value.get("position")
    position = (
        None
        if position_value is None
        else _positive_int(position_value, "position")
    )
    if (status == "queued") != (position is not None):
        raise DurableQueueResponseError("queue item position does not match status")

    cid = _required_text(value.get("cid"), "cid")
    sid = _required_text(value.get("sid"), "sid")
    if cid != expected_cid or sid != expected_sid:
        raise DurableQueueResponseError("queue item does not match session")

    submission_id = _submission_id(value.get("submission_id"))
    client_message_id = _required_text(
        value.get("client_message_id"),
        "client_message_id",
    )
    if len(client_message_id) > 128:
        raise DurableQueueResponseError("client_message_id exceeds 128 characters")
    turn_id_value = _required_text(value.get("turn_id"), "turn_id")
    try:
        turn_id = normalize_turn_id(turn_id_value)
    except ValueError as error:
        raise DurableQueueResponseError(str(error)) from error

    return DurableQueueItem(
        queue_seq=_positive_int(value.get("queue_seq"), "queue_seq"),
        cid=cid,
        sid=sid,
        submission_id=submission_id,
        client_message_id=client_message_id,
        turn_id=turn_id,
        position=position,
        status=status,
        input=_input_snapshot(value.get("input")),
        created_at=_timestamp(value.get("created_at"), "created_at"),
        updated_at=_timestamp(value.get("updated_at"), "updated_at"),
        started_at=_optional_timestamp(value.get("started_at"), "started_at"),
        deleted_at=_optional_timestamp(value.get("deleted_at"), "deleted_at"),
    )


def _input_snapshot(value: JsonValue) -> QueueInputSnapshot:
    """校验 Queue item 中不含凭据的输入投影。"""
    if not isinstance(value, dict):
        raise DurableQueueResponseError("queue input is invalid")
    _require_keys(value, {"text", "attachments", "extras"}, "queue input")
    text = value.get("text")
    attachments_value = value.get("attachments")
    extras_value = value.get("extras")
    if (
        not isinstance(text, str)
        or not isinstance(attachments_value, list)
        or not isinstance(extras_value, dict)
        or any(not isinstance(item, dict) for item in attachments_value)
    ):
        raise DurableQueueResponseError("queue input is invalid")
    if not text.strip() and not attachments_value:
        raise DurableQueueResponseError("queue input is empty")
    return QueueInputSnapshot(
        text=text,
        attachments=tuple(copy.deepcopy(item) for item in attachments_value),
        extras=copy.deepcopy(extras_value),
    )


def _item_status(value: JsonValue) -> QueueItemStatus:
    """把队列状态收窄为正式字面量。"""
    if value == "queued":
        return value
    if value == "started":
        return value
    if value == "deleted":
        return value
    raise DurableQueueResponseError("queue item status is invalid")


def _submission_id(value: JsonValue) -> str:
    """校验响应中的 Queue submission identity。"""
    if not isinstance(value, str):
        raise DurableQueueResponseError("submission_id is invalid")
    try:
        return normalize_submission_id(value)
    except ValueError as error:
        raise DurableQueueResponseError(str(error)) from error


def _required_text(value: JsonValue, field_name: str) -> str:
    """读取非空文本字段。"""
    if not isinstance(value, str) or not value.strip():
        raise DurableQueueResponseError(f"{field_name} is required")
    return value.strip()


def _positive_int(value: JsonValue, field_name: str) -> int:
    """读取正整数协议字段。"""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise DurableQueueResponseError(f"{field_name} must be a positive integer")
    return value


def _non_negative_int(value: JsonValue, field_name: str) -> int:
    """读取非负整数协议字段。"""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DurableQueueResponseError(
            f"{field_name} must be a non-negative integer"
        )
    return value


def _timestamp(value: JsonValue, field_name: str) -> float:
    """读取有限数值时间戳。"""
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        raise DurableQueueResponseError(f"{field_name} must be a finite timestamp")
    return float(value)


def _optional_timestamp(value: JsonValue, field_name: str) -> float | None:
    """读取可空的有限数值时间戳。"""
    if value is None:
        return None
    return _timestamp(value, field_name)


def _require_keys(
    value: dict[str, JsonValue],
    expected: set[str],
    field_name: str,
) -> None:
    """要求响应对象与服务端 extra=forbid 契约具有完全相同的字段。"""
    if set(value) != expected:
        raise DurableQueueResponseError(f"{field_name} fields are invalid")


if __name__ == '__main__':
    pass
