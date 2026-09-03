# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import copy
import dataclasses
import typing
from collections.abc import Mapping
from dataclasses import (
    dataclass,
    field,
)

from agent.protocol import (
    CanonicalItem,
    ModelEvent,
)
from agent.protocol.json_value import ThawedJsonValue

_TERMINAL_ITEM_STATUSES = frozenset({
    "completed",
    "failed",
    "cancelled",
    "reconciliation_required",
})

_ITEM_STATUS_TRANSITIONS = {
    "registered": frozenset({
        "registered",
        "in_progress",
        "waiting_result",
        "waiting_approval",
        "result_received",
        *_TERMINAL_ITEM_STATUSES,
    }),
    "in_progress": frozenset({"in_progress", *_TERMINAL_ITEM_STATUSES}),
    "waiting_result": frozenset({
        "waiting_result",
        "result_received",
        *_TERMINAL_ITEM_STATUSES,
    }),
    "waiting_approval": frozenset({
        "waiting_approval",
        "result_received",
        *_TERMINAL_ITEM_STATUSES,
    }),
    "result_received": frozenset({
        "result_received",
        *_TERMINAL_ITEM_STATUSES,
    }),
    "completed": frozenset({"completed"}),
    "failed": frozenset({"failed"}),
    "cancelled": frozenset({"cancelled"}),
    "reconciliation_required": frozenset({"reconciliation_required"}),
}

_COMMON_EVENT_FIELDS = frozenset({
    "type",
    "proto",
    "cid",
    "sid",
    "turn_id",
    "event_seq",
    "presentation_epoch",
    "round",
    "display",
    "item_id",
    "item_kind",
    "item_status",
})

_TEXT_META_FIELDS = (
    "annotations",
    "citations",
    "sources",
    "source_count",
    "builtin_call_ids",
)

_ItemKey: typing.TypeAlias = tuple[str, int, int, int]


@dataclass(slots=True)
class _CanonicalItemState:
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
    payload: dict[str, typing.Any] = field(default_factory=dict)
    superseded: bool = False
    superseded_by_epoch: int | None = None
    superseded_by_attempt: int | None = None

    def snapshot(self) -> CanonicalItem:
        """冻结当前 reducer 状态供前端读取。"""
        return CanonicalItem(
            cid=self.cid,
            sid=self.sid,
            turn_id=self.turn_id,
            item_id=self.item_id,
            item_kind=self.item_kind,
            item_status=self.item_status,
            presentation_epoch=self.presentation_epoch,
            round_no=self.round_no,
            attempt=self.attempt,
            first_event_seq=self.first_event_seq,
            last_event_seq=self.last_event_seq,
            last_event_type=self.last_event_type,
            payload=self.payload,
            superseded=self.superseded,
            superseded_by_epoch=self.superseded_by_epoch,
            superseded_by_attempt=self.superseded_by_attempt,
        )


class CanonicalItemReducer:
    """把单个 Turn 的正式事件归约为与前端无关的 Canonical Items。"""

    def __init__(self, *, cid: str, sid: str, turn_id: str) -> None:
        """绑定唯一 Turn 坐标并初始化展示代次状态。"""
        self._cid = _required_text(cid, "cid")
        self._sid = _required_text(sid, "sid")
        self._turn_id = _required_text(turn_id, "turn_id")
        self._items: dict[_ItemKey, _CanonicalItemState] = {}
        self._active_attempts: dict[tuple[int, int], int] = {}
        self._pending_text_meta: dict[
            _ItemKey,
            tuple[int, int, dict[str, typing.Any]],
        ] = {}
        self._retired_response_items: set[tuple[str, int, int]] = set()
        self._approval_snapshot_watermark = 0
        self._approval_snapshot_statuses: dict[str, str] = {}

    @property
    def canonical_items(self) -> tuple[CanonicalItem, ...]:
        """返回按首次事件顺序排列且未被替换的 Item 快照。"""
        return tuple(
            state.snapshot()
            for state in sorted(
                self._items.values(),
                key=lambda item: (item.first_event_seq, item.item_id),
            )
            if not state.superseded
        )

    @property
    def item_history(self) -> tuple[CanonicalItem, ...]:
        """返回包含被替换展示版本的完整审计快照。"""
        return tuple(
            state.snapshot()
            for state in sorted(
                self._items.values(),
                key=lambda item: (item.first_event_seq, item.item_id),
            )
        )

    @property
    def pending_approval_items(self) -> tuple[CanonicalItem, ...]:
        """返回快照优先级归约后仍等待决定的审批 Item。"""
        return tuple(
            item
            for item in self.canonical_items
            if item.item_kind == "approval"
            and item.item_status == "waiting_approval"
        )

    @property
    def assistant_text(self) -> str:
        """返回当前未被替换的正文 Item 聚合文本。"""
        parts = [
            str(state.payload.get("text") or "")
            for state in sorted(
                self._items.values(),
                key=lambda item: (item.first_event_seq, item.item_id),
            )
            if not state.superseded and state.item_kind == "text"
        ]
        return _join_text(parts).strip()

    @property
    def sources(self) -> tuple[ThawedJsonValue, ...]:
        """返回 active 正文和内置工具 Item 中按首次出现去重的来源。"""
        collected: list[ThawedJsonValue] = []
        for item in self.canonical_items:
            if item.item_kind not in {"text", "builtin_tool"}:
                continue
            sources = item.payload_value().get("sources")
            if not isinstance(sources, list):
                continue
            for source in sources:
                if source not in collected:
                    collected.append(source)
        return tuple(collected)

    @staticmethod
    def _merge_event_payload(
        state: _CanonicalItemState,
        event: ModelEvent,
    ) -> None:
        """按事件族合并可渲染载荷。"""
        if event.type == "text.delta":
            state.payload["text"] = (
                str(state.payload.get("text") or "")
                + str(getattr(event, "text", "") or "")
            )
            return
        if event.type == "text.done":
            final_text = getattr(event, "final_text", None)
            if final_text is not None:
                state.payload["text"] = str(final_text)
            else:
                state.payload.setdefault("text", "")
            return
        if event.type == "text.meta":
            state.payload.update(_text_meta_payload(event))
            return
        state.payload.update(_event_payload(event))

    def _apply_approval_snapshot_item(
        self,
        item: object,
        *,
        watermark: int,
    ) -> None:
        """把一个权威审批信封合并到现有 Item 版本。"""
        approval_id = _required_text(
            getattr(item, "approval_id", None),
            "approval snapshot approval_id",
        )
        if getattr(item, "turn_id", None) != self._turn_id:
            raise ValueError("approval snapshot item does not belong to reducer turn")
        status = _required_text(
            getattr(item, "status", None),
            "approval snapshot status",
        )
        item_status = _approval_item_status(status)
        raw_approval = getattr(item, "approval", None)
        if not isinstance(raw_approval, Mapping):
            raise TypeError("approval snapshot item payload must be an object")
        approval = dict(raw_approval)
        envelope_item_id = str(approval.get("item_id") or approval_id).strip()
        if envelope_item_id != approval_id:
            raise ValueError("approval snapshot item_id does not match approval_id")
        envelope_item_kind = str(approval.get("item_kind") or "approval").strip()
        if envelope_item_kind != "approval":
            raise ValueError("approval snapshot item_kind must be approval")

        event_seq = approval.get("event_seq")
        first_event_seq = (
            event_seq
            if isinstance(event_seq, int)
               and not isinstance(event_seq, bool)
               and event_seq > 0
            else watermark
        )
        if first_event_seq > watermark:
            raise ValueError("approval snapshot event_seq exceeds watermark")
        presentation_epoch = approval.get("presentation_epoch", 1)
        if (
            isinstance(presentation_epoch, bool)
            or not isinstance(presentation_epoch, int)
            or presentation_epoch < 1
        ):
            raise ValueError("approval snapshot presentation_epoch must be positive")
        raw_round = approval.get("round")
        round_no = (
            1
            if raw_round is None
            else _positive_int(raw_round, "approval snapshot round")
        )
        attempt = self._active_attempts.get(
            (presentation_epoch, round_no),
            1,
        )

        state = self._active_item_state(approval_id)
        if state is None:
            key = (approval_id, presentation_epoch, round_no, attempt)
            self._reject_parallel_item_revision(item_id=approval_id, key=key)
            state = _CanonicalItemState(
                cid=self._cid,
                sid=self._sid,
                turn_id=self._turn_id,
                item_id=approval_id,
                item_kind="approval",
                item_status=item_status,
                presentation_epoch=presentation_epoch,
                round_no=round_no,
                attempt=attempt,
                first_event_seq=first_event_seq,
                last_event_seq=watermark,
                last_event_type="approval.snapshot",
            )
            self._items[key] = state
        else:
            if state.item_kind != "approval":
                raise ValueError("approval snapshot conflicts with another item kind")
            if state.last_event_seq > watermark:
                raise ValueError("approval snapshot is older than canonical item")
            _validate_status_transition(state.item_status, item_status)
            state.item_status = item_status
            state.last_event_seq = max(state.last_event_seq, watermark)
            state.last_event_type = "approval.snapshot"

        ack = getattr(item, "ack", None)
        if ack is not None and not isinstance(ack, Mapping):
            raise TypeError("approval snapshot ack must be an object or null")
        approval["snapshot_status"] = status
        approval["ack"] = dict(ack) if isinstance(ack, Mapping) else None
        state.payload.update(approval)
        self._approval_snapshot_statuses[approval_id] = status

    def _response_position(self, event: ModelEvent) -> tuple[int, int]:
        """返回事件所属模型 round 和当前 provider attempt。"""
        raw_round = getattr(event, "round", None)
        round_no = 1 if raw_round is None else _positive_int(
            raw_round,
            f"{event.type} round",
        )
        attempt = self._active_attempts.get(
            (event.presentation_epoch, round_no),
            1,
        )
        return round_no, attempt

    def _apply_retry(self, event: ModelEvent) -> None:
        """标记当前模型 round 的旧 provider attempt 已被替换。"""
        round_no = _positive_int(
            getattr(event, "round", None),
            "turn.retrying round",
        )
        attempt = _positive_int(
            getattr(event, "attempt", None),
            "turn.retrying attempt",
        )
        response_key = (event.presentation_epoch, round_no)
        previous_attempt = self._active_attempts.get(response_key, 1)
        if attempt <= previous_attempt:
            raise ValueError("turn.retrying attempt must increase")
        self._active_attempts[response_key] = attempt

        supersedes_item_id = str(
            getattr(event, "supersedes_item_id", "") or ""
        ).strip()
        for state in self._items.values():
            named = bool(supersedes_item_id) and (
                state.item_id == supersedes_item_id
            )
            older_attempt = (
                state.presentation_epoch == event.presentation_epoch
                and state.round_no == round_no
                and state.attempt < attempt
            )
            if named or older_attempt:
                state.superseded = True
                state.superseded_by_attempt = attempt
                self._retired_response_items.add((
                    state.item_id,
                    state.presentation_epoch,
                    state.round_no,
                ))
        self._pending_text_meta = {
            key: payload
            for key, payload in self._pending_text_meta.items()
            if not (
                key[1] == event.presentation_epoch
                and key[2] == round_no
                and key[3] < attempt
            )
        }

    def _apply_presentation_superseded(self, event: ModelEvent) -> None:
        """把被 Worker 展示代次取代的 Item 移出 canonical 视图。"""
        superseded_epoch = _positive_int(
            getattr(event, "superseded_epoch", None),
            "presentation.superseded superseded_epoch",
        )
        if superseded_epoch >= event.presentation_epoch:
            raise ValueError("superseded presentation epoch must precede current epoch")
        for state in self._items.values():
            if state.presentation_epoch <= superseded_epoch:
                state.superseded = True
                state.superseded_by_epoch = event.presentation_epoch
        self._active_attempts = {
            key: attempt
            for key, attempt in self._active_attempts.items()
            if key[0] > superseded_epoch
        }
        self._pending_text_meta = {
            key: payload
            for key, payload in self._pending_text_meta.items()
            if key[1] > superseded_epoch
        }

    def _reject_parallel_item_revision(
        self,
        *,
        item_id: str,
        key: _ItemKey,
    ) -> None:
        """拒绝同一稳定 Item 同时存在两个活动展示版本。"""
        if any(
            existing_key != key
            and state.item_id == item_id
            and not state.superseded
            for existing_key, state in self._items.items()
        ):
            raise ValueError("canonical item has multiple active presentations")

    def _active_item_state(self, item_id: str) -> _CanonicalItemState | None:
        """返回稳定 Item 当前未被展示替换的版本。"""
        return next(
            (
                state
                for state in reversed(tuple(self._items.values()))
                if state.item_id == item_id and not state.superseded
            ),
            None,
        )

    def _validate_coordinates(self, event: ModelEvent) -> None:
        """拒绝把其他 Session 或 Turn 的事件写入当前 reducer。"""
        if (
            event.cid != self._cid
            or event.sid != self._sid
            or event.turn_id != self._turn_id
        ):
            raise ValueError("canonical item event coordinates do not match reducer")

    def apply(self, event: ModelEvent) -> CanonicalItem | None:
        """应用一条已完成公共字段校验的正式协议事件。"""
        self._validate_coordinates(event)
        if event.type == "turn.retrying":
            self._apply_retry(event)
            return None
        if event.type == "presentation.superseded":
            self._apply_presentation_superseded(event)
            return None

        item_id = str(getattr(event, "item_id", "") or "").strip()
        if not item_id:
            return None
        item_kind = _required_text(
            getattr(event, "item_kind", None),
            f"{event.type} item_kind",
        )
        item_status = _required_text(
            getattr(event, "item_status", None),
            f"{event.type} item_status",
        )
        event_seq = _positive_int(event.event_seq, f"{event.type} event_seq")
        if event.type == "tool.approval_required":
            snapshot_status = self._approval_snapshot_statuses.get(item_id)
            if (
                snapshot_status is not None
                and event_seq <= self._approval_snapshot_watermark
            ):
                state = self._active_item_state(item_id)
                return state.snapshot() if state is not None else None
            item_status = _approval_item_status(
                getattr(event, "status", "pending")
            )
        round_no, attempt = self._response_position(event)
        if (
                item_id,
                event.presentation_epoch,
                round_no,
        ) in self._retired_response_items:
            return None
        key = (item_id, event.presentation_epoch, round_no, attempt)
        if item_status not in _ITEM_STATUS_TRANSITIONS:
            raise ValueError("canonical item status is invalid")

        if event.type == "text.meta" and key not in self._items:
            pending_first_seq, pending_last_seq, pending_payload = (
                self._pending_text_meta.get(
                    key,
                    (event_seq, 0, {}),
                )
            )
            if event_seq <= pending_last_seq:
                raise ValueError("canonical item event sequence must increase")
            pending_payload.update(_text_meta_payload(event))
            self._pending_text_meta[key] = (
                pending_first_seq,
                event_seq,
                pending_payload,
            )
            return None

        state = self._items.get(key)
        if state is None:
            self._reject_parallel_item_revision(item_id=item_id, key=key)
            pending = self._pending_text_meta.pop(key, None)
            if pending is None:
                pending_first_seq = event_seq
                pending_payload = {}
            else:
                pending_first_seq, pending_last_seq, pending_payload = pending
                if item_kind != "text":
                    raise ValueError("text metadata cannot attach to another item kind")
                if event_seq <= pending_last_seq:
                    raise ValueError("canonical item event sequence must increase")
            state = _CanonicalItemState(
                cid=self._cid,
                sid=self._sid,
                turn_id=self._turn_id,
                item_id=item_id,
                item_kind=item_kind,
                item_status=item_status,
                presentation_epoch=event.presentation_epoch,
                round_no=round_no,
                attempt=attempt,
                first_event_seq=pending_first_seq,
                last_event_seq=event_seq,
                last_event_type=event.type,
                payload=pending_payload,
            )
            self._items[key] = state
        else:
            if state.superseded:
                raise ValueError("superseded canonical item cannot receive new events")
            if state.item_kind != item_kind:
                raise ValueError("canonical item kind cannot change")
            _validate_status_transition(state.item_status, item_status)
            if event_seq <= state.last_event_seq:
                raise ValueError("canonical item event sequence must increase")
            state.item_status = item_status
            state.last_event_seq = event_seq
            state.last_event_type = event.type

        self._merge_event_payload(state, event)
        return state.snapshot()

    def apply_approval_snapshot(
        self,
        snapshot: object,
    ) -> tuple[CanonicalItem, ...]:
        """按快照水位归约审批状态，但不改变事件确认游标。"""
        if (
            getattr(snapshot, "cid", None) != self._cid
            or getattr(snapshot, "sid", None) != self._sid
            or getattr(snapshot, "turn_id", None) != self._turn_id
        ):
            raise ValueError("approval snapshot coordinates do not match reducer")
        watermark = _nonnegative_int(
            getattr(snapshot, "last_event_seq", None),
            "approval snapshot last_event_seq",
        )
        if watermark < self._approval_snapshot_watermark:
            raise ValueError("approval snapshot watermark cannot move backwards")
        approvals = getattr(snapshot, "approvals", None)
        if not isinstance(approvals, (tuple, list)):
            raise TypeError("approval snapshot approvals must be a sequence")
        if approvals and watermark < 1:
            raise ValueError("approval snapshot with items requires a positive watermark")

        candidate = copy.deepcopy(self)
        for item in approvals:
            candidate._apply_approval_snapshot_item(
                item,
                watermark=watermark,
            )
        candidate._approval_snapshot_watermark = watermark
        self._items = candidate._items
        self._approval_snapshot_statuses = (
            candidate._approval_snapshot_statuses
        )
        self._approval_snapshot_watermark = watermark
        return self.pending_approval_items


def _validate_status_transition(previous: str, current: str) -> None:
    """校验 Canonical Item 状态只能单向收敛。"""
    allowed = _ITEM_STATUS_TRANSITIONS.get(previous)
    if allowed is None or current not in allowed:
        raise ValueError(
            f"canonical item status cannot transition from {previous} to {current}"
        )


def _event_payload(event: ModelEvent) -> dict[str, typing.Any]:
    """提取不含公共坐标和 Item 投影的事件载荷。"""
    raw_payload = getattr(event, "payload", None)
    if isinstance(raw_payload, Mapping):
        return {
            key: value
            for key, value in raw_payload.items()
            if key not in _COMMON_EVENT_FIELDS
        }
    if dataclasses.is_dataclass(event):
        values = dataclasses.asdict(event)
    else:
        values = dict(vars(event)) if hasattr(event, "__dict__") else {}
    return {
        key: value
        for key, value in values.items()
        if key not in _COMMON_EVENT_FIELDS and value is not None
    }


def _text_meta_payload(event: ModelEvent) -> dict[str, typing.Any]:
    """提取正文 Item 的来源、引用和内置工具关联。"""
    return {
        field_name: getattr(event, field_name)
        for field_name in _TEXT_META_FIELDS
        if getattr(event, field_name, None) is not None
    }


def _required_text(value: typing.Any, field_name: str) -> str:
    """返回经过规范化的必填文本。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"canonical item {field_name} is required")
    return value.strip()


def _positive_int(value: typing.Any, field_name: str) -> int:
    """返回经过校验的正整数。"""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"canonical item {field_name} must be positive")
    return value


def _nonnegative_int(value: typing.Any, field_name: str) -> int:
    """返回经过校验的非负整数。"""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"canonical item {field_name} must be non-negative")
    return value


def _approval_item_status(value: typing.Any) -> str:
    """把审批快照状态映射为 Canonical Item 生命周期。"""
    status = str(value or "").strip()
    mapped = {
        "pending": "waiting_approval",
        "resolved": "completed",
        "expired": "cancelled",
        "cancelled": "cancelled",
    }
    if status not in mapped:
        raise ValueError("approval snapshot status is invalid")
    return mapped[status]


def _join_text(parts: list[str]) -> str:
    """按 Item 边界拼接当前 canonical 正文。"""
    output = ""
    for part in parts:
        if not part:
            continue
        if output and not output.endswith("\n") and not part.startswith("\n"):
            output += "\n"
        output += part
    return output


if __name__ == '__main__':
    pass
