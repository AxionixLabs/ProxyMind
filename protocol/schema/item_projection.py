# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

ItemKind: typing.TypeAlias = typing.Literal[
    "context_compaction",
    "message",
    "text",
    "reasoning",
    "tool_call",
    "tool_output",
    "builtin_tool",
    "approval",
    "custom",
]

ItemStatus: typing.TypeAlias = typing.Literal[
    "registered",
    "in_progress",
    "waiting_result",
    "waiting_approval",
    "result_received",
    "completed",
    "failed",
    "cancelled",
    "reconciliation_required",
]

ITEM_KINDS = frozenset(typing.get_args(ItemKind))

ITEM_STATUSES = frozenset(typing.get_args(ItemStatus))


def default_item_projection(
    event: typing.Any,
    *,
    item_id: str,
    item_kind: ItemKind,
    item_status: ItemStatus,
) -> None:
    """为代码内直接构造的类型化事件补齐确定性 Item 投影。"""
    if not event.item_id:
        object.__setattr__(event, "item_id", item_id)
    if event.item_kind == "custom":
        object.__setattr__(event, "item_kind", item_kind)
    if event.item_status == "registered":
        object.__setattr__(event, "item_status", item_status)


def validate_item_fields(
    payload: dict[str, typing.Any],
    *,
    event_type: str,
    source_id: str,
    expected_kind: ItemKind,
    expected_status: ItemStatus,
) -> dict[str, typing.Any]:
    """校验可展示事件的 Canonical Item 投影。"""
    item_id = _required_text(payload.get("item_id"), f"{event_type} item_id")
    item_kind = _required_text(
        payload.get("item_kind"),
        f"{event_type} item_kind",
    )
    item_status = _required_text(
        payload.get("item_status"),
        f"{event_type} item_status",
    )
    if item_kind not in ITEM_KINDS:
        raise ValueError(f"{event_type} item_kind is invalid")
    if item_status not in ITEM_STATUSES:
        raise ValueError(f"{event_type} item_status is invalid")
    if item_kind != expected_kind:
        raise ValueError(f"{event_type} item_kind does not match event type")
    if item_status != expected_status:
        raise ValueError(
            f"{event_type} item_status does not match event lifecycle"
        )
    if source_id and item_id != source_id:
        raise ValueError(f"{event_type} item_id does not match domain identity")

    return {
        "item_id": item_id,
        "item_kind": item_kind,
        "item_status": item_status,
    }


def reject_item_projection(
    payload: dict[str, typing.Any],
    *,
    event_type: str,
) -> None:
    """拒绝控制事件和未启用扩展携带 Item 投影。"""
    if any(
        field in payload
        for field in ("item_id", "item_kind", "item_status")
    ):
        raise ValueError(
            f"{event_type} control event must not carry Item projection"
        )


def tool_output_item_status(value: typing.Any) -> ItemStatus:
    """把工具输出结果映射为正式 Item 生命周期状态。"""
    status = str(value or "").strip().lower()
    mapped: dict[str, ItemStatus] = {
        "completed": "completed",
        "failed": "failed",
        "cancelled": "cancelled",
        "declined": "result_received",
    }
    if status not in mapped:
        raise ValueError("tool.output status is invalid")
    return mapped[status]


def builtin_done_item_status(value: typing.Any) -> ItemStatus:
    """把内置工具完成结果映射为正式 Item 生命周期状态。"""
    status = str(value or "").strip().lower()
    mapped = {
        "failed": "failed",
        "cancelled": "cancelled",
        "incomplete": "failed",
    }.get(status, "completed")
    return mapped


def _required_text(value: typing.Any, field_name: str) -> str:
    """读取必填非空文本字段。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} is required")
    return value.strip()


if __name__ == '__main__':
    pass
