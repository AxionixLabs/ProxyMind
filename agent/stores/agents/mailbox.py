# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import time
import typing
from collections import deque
from dataclasses import dataclass

from agent.application.turns.context import AgentContext
from agent.domain.agents import MAX_AGENT_MESSAGE_CHARS
from protocol.schema.identifiers import short_uid

AgentMailboxEventKind = typing.Literal["message", "queue", "status"]

MAX_MAILBOX_EVENTS = 1000
MAX_MAILBOX_UPDATES = 50
MAX_MAILBOX_CONTEXT_CHARS = 20_000


@dataclass(frozen=True, slots=True)
class AgentMailboxEvent:
    """描述可持久化的执行主体通信或状态事件。"""
    event_id: str
    sequence: int
    kind: AgentMailboxEventKind
    created_at_ms: int
    source_agent_id: str
    source_task_path: str
    recipient_agent_id: str = ""
    recipient_task_path: str = ""
    message: str = ""
    status: str = ""
    submission_id: str = ""
    queued_count: int = 0
    detail: str = ""

    def __post_init__(self) -> None:
        """校验事件的稳定字段和类型特定载荷。"""
        event_id = str(self.event_id or "").strip()
        source_agent_id = str(self.source_agent_id or "").strip()
        source_task_path = str(self.source_task_path or "").strip()
        recipient_agent_id = str(self.recipient_agent_id or "").strip()
        recipient_task_path = str(self.recipient_task_path or "").strip()
        message = str(self.message or "").strip()
        status = str(self.status or "").strip()
        submission_id = str(self.submission_id or "").strip()
        detail = str(self.detail or "").strip()

        if not event_id:
            raise ValueError("mailbox event id is required")
        if self.kind not in {"message", "queue", "status"}:
            raise ValueError("mailbox event kind is invalid")
        if (
            isinstance(self.sequence, bool)
            or not isinstance(self.sequence, int)
            or self.sequence <= 0
            or isinstance(self.created_at_ms, bool)
            or not isinstance(self.created_at_ms, int)
            or self.created_at_ms <= 0
        ):
            raise ValueError("mailbox event ordering fields must be positive")
        if not source_agent_id or not source_task_path:
            raise ValueError("mailbox event source is required")
        if self.kind == "message":
            if not recipient_agent_id or not recipient_task_path:
                raise ValueError("mailbox message recipient is required")
            if not message:
                raise ValueError("mailbox message is required")
        if self.kind == "queue" and not submission_id:
            raise ValueError("mailbox queue submission is required")
        if self.kind == "status" and not status:
            raise ValueError("mailbox status is required")
        if (
            isinstance(self.queued_count, bool)
            or not isinstance(self.queued_count, int)
            or self.queued_count < 0
        ):
            raise ValueError("mailbox queued count must be non-negative")

        object.__setattr__(self, "event_id", event_id)
        object.__setattr__(self, "source_agent_id", source_agent_id)
        object.__setattr__(self, "source_task_path", source_task_path)
        object.__setattr__(self, "recipient_agent_id", recipient_agent_id)
        object.__setattr__(self, "recipient_task_path", recipient_task_path)
        object.__setattr__(self, "message", message)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "submission_id", submission_id)
        object.__setattr__(self, "detail", detail)

    def to_dict(self) -> dict[str, typing.Any]:
        """返回可直接序列化的事件载荷。"""
        return {
            "event_id": self.event_id,
            "sequence": self.sequence,
            "kind": self.kind,
            "created_at_ms": self.created_at_ms,
            "source_agent_id": self.source_agent_id,
            "source_task_path": self.source_task_path,
            "recipient_agent_id": self.recipient_agent_id,
            "recipient_task_path": self.recipient_task_path,
            "message": self.message,
            "status": self.status,
            "submission_id": self.submission_id,
            "queued_count": self.queued_count,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class AgentMailboxSnapshot:
    """保存有界事件日志与各读取主体的消费位置。"""
    sequence: int = 0
    events: tuple[AgentMailboxEvent, ...] = ()
    consumed: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def __post_init__(self) -> None:
        """校验邮箱快照的顺序、事件和消费引用。"""
        if (
            isinstance(self.sequence, bool)
            or not isinstance(self.sequence, int)
            or self.sequence < 0
        ):
            raise ValueError("mailbox sequence must be non-negative")
        if not isinstance(self.events, tuple) or any(
            not isinstance(event, AgentMailboxEvent)
            for event in self.events
        ):
            raise TypeError("mailbox snapshot events must be a tuple")

        sequences = [event.sequence for event in self.events]
        event_ids = [event.event_id for event in self.events]
        if sequences != sorted(sequences) or len(sequences) != len(set(sequences)):
            raise ValueError("mailbox snapshot event order is invalid")
        if sequences and sequences[-1] > self.sequence:
            raise ValueError("mailbox snapshot sequence is behind its events")
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("mailbox snapshot event ids must be unique")

        readers: set[str] = set()
        known_ids = set(event_ids)
        for item in self.consumed:
            if (
                not isinstance(item, tuple)
                or len(item) != 2
                or not isinstance(item[0], str)
                or not item[0]
                or not isinstance(item[1], tuple)
                or any(not isinstance(event_id, str) for event_id in item[1])
            ):
                raise TypeError("mailbox consumed cursor is invalid")
            reader, consumed_ids = item
            if reader in readers:
                raise ValueError("mailbox consumed readers must be unique")
            if len(consumed_ids) != len(set(consumed_ids)):
                raise ValueError("mailbox consumed event ids must be unique")
            if not set(consumed_ids).issubset(known_ids):
                raise ValueError("mailbox consumed event is missing")
            readers.add(reader)

    @classmethod
    def empty(cls) -> "AgentMailboxSnapshot":
        """创建空邮箱快照。"""
        return cls()


class AgentMailboxStore:
    """保存有界事件日志和各调用主体的消费位置。"""

    def __init__(self, *, capacity: int = MAX_MAILBOX_EVENTS) -> None:
        if (
            isinstance(capacity, bool)
            or not isinstance(capacity, int)
            or capacity <= 0
        ):
            raise ValueError("mailbox capacity must be a positive integer")
        self._capacity = capacity
        self._sequence = 0
        self._events: deque[AgentMailboxEvent] = deque()
        self._consumed: dict[str, set[str]] = {}
        self._claims: dict[str, tuple[str, str]] = {}

    @classmethod
    def from_snapshot(
        cls,
        snapshot: AgentMailboxSnapshot,
        *,
        capacity: int = MAX_MAILBOX_EVENTS,
    ) -> "AgentMailboxStore":
        """从已校验快照重建有界邮箱。"""
        if not isinstance(snapshot, AgentMailboxSnapshot):
            raise TypeError("mailbox snapshot is required")
        store = cls(capacity=capacity)
        store._sequence = snapshot.sequence
        store._events.extend(snapshot.events)
        store._consumed = {
            reader: set(event_ids)
            for reader, event_ids in snapshot.consumed
        }
        store._trim()
        return store

    def snapshot(self) -> AgentMailboxSnapshot:
        """返回可持久化的当前邮箱快照。"""
        return AgentMailboxSnapshot(
            sequence=self._sequence,
            events=tuple(self._events),
            consumed=tuple(
                (reader, tuple(sorted(event_ids)))
                for reader, event_ids in sorted(self._consumed.items())
                if event_ids
            ),
        )

    def publish(
        self,
        kind: AgentMailboxEventKind,
        source: AgentContext,
        *,
        recipient: AgentContext | None = None,
        message: str = "",
        status: str = "",
        submission_id: str = "",
        queued_count: int = 0,
        detail: str = "",
    ) -> AgentMailboxEvent:
        """追加一项通信或状态事件。"""
        normalized_message = str(message or "").strip()

        encoded_message_chars = len(json.dumps(
            normalized_message,
            ensure_ascii=False,
        ))
        if encoded_message_chars > MAX_AGENT_MESSAGE_CHARS:
            raise ValueError(
                f"agent message exceeds {MAX_AGENT_MESSAGE_CHARS} characters"
            )

        self._sequence += 1
        event = AgentMailboxEvent(
            event_id=short_uid(12),
            sequence=self._sequence,
            kind=kind,
            created_at_ms=time.time_ns() // 1_000_000,
            source_agent_id=source.agent_id,
            source_task_path=source.task_path,
            recipient_agent_id=recipient.agent_id if recipient else "",
            recipient_task_path=recipient.task_path if recipient else "",
            message=normalized_message,
            status=str(status or "").strip(),
            submission_id=str(submission_id or "").strip(),
            queued_count=queued_count,
            detail=str(detail or "").strip(),
        )
        self._events.append(event)
        self._trim()
        return event

    def take_updates(
        self,
        reader_agent_id: str,
        source_agent_ids: typing.Collection[str],
        *,
        limit: int = MAX_MAILBOX_UPDATES,
    ) -> tuple[AgentMailboxEvent, ...]:
        """返回指定来源的未读事件并标记已消费。"""
        _require_positive_limit(limit, "mailbox update limit")

        reader = str(reader_agent_id or "").strip()
        sources = {str(item or "").strip() for item in source_agent_ids}

        if not reader or not sources or "" in sources:
            raise ValueError("mailbox reader and sources are required")

        consumed = self._consumed.setdefault(reader, set())

        selected: list[AgentMailboxEvent] = []
        for event in self._events:
            if (
                event.event_id in consumed
                or event.event_id in self._claims
                or event.source_agent_id not in sources
            ):
                continue
            if event.kind == "message" and event.recipient_agent_id != reader:
                continue
            selected.append(event)
            if len(selected) >= limit:
                break

        consumed.update(event.event_id for event in selected)
        return tuple(selected)

    def take_messages(
        self,
        reader_agent_id: str,
        *,
        limit: int = MAX_MAILBOX_UPDATES,
        max_chars: int = MAX_MAILBOX_CONTEXT_CHARS,
    ) -> tuple[AgentMailboxEvent, ...]:
        """返回发给指定主体的未读消息。"""
        _require_positive_limit(limit, "mailbox message limit")
        _require_positive_limit(max_chars, "mailbox context character limit")

        reader = str(reader_agent_id or "").strip()
        if not reader:
            raise ValueError("mailbox reader is required")

        consumed = self._consumed.setdefault(reader, set())
        selected = self._select_messages(reader, limit=limit, max_chars=max_chars)

        consumed.update(event.event_id for event in selected)

        return selected

    def claim_message(
        self,
        reader_agent_id: str,
        event_id: str,
        owner_id: str,
    ) -> bool:
        """为指定投递者临时锁定一项未读消息。"""
        reader = str(reader_agent_id or "").strip()
        target_event_id = str(event_id or "").strip()
        owner = str(owner_id or "").strip()

        if not reader or not target_event_id or not owner:
            raise ValueError("mailbox reader, event id, and owner are required")

        event = next(
            (item for item in self._events if item.event_id == target_event_id),
            None,
        )
        if (
            event is None
            or event.kind != "message"
            or event.recipient_agent_id != reader
            or event.event_id in self._consumed.get(reader, set())
            or event.event_id in self._claims
        ):
            return False

        self._claims[target_event_id] = (reader, owner)
        return True

    def claim_messages(
        self,
        reader_agent_id: str,
        owner_id: str,
        *,
        limit: int = MAX_MAILBOX_UPDATES,
        max_chars: int = MAX_MAILBOX_CONTEXT_CHARS,
    ) -> tuple[AgentMailboxEvent, ...]:
        """为一次轮次临时锁定指定主体的未读消息。"""
        _require_positive_limit(limit, "mailbox message limit")
        _require_positive_limit(max_chars, "mailbox context character limit")

        reader = str(reader_agent_id or "").strip()
        owner = str(owner_id or "").strip()

        if not reader or not owner:
            raise ValueError("mailbox reader and owner are required")

        selected = self._select_messages(reader, limit=limit, max_chars=max_chars)
        for event in selected:
            self._claims[event.event_id] = (reader, owner)
        return selected

    def acknowledge_claim(
        self,
        reader_agent_id: str,
        owner_id: str,
        event_ids: typing.Collection[str],
    ) -> tuple[str, ...]:
        """确认指定投递者已完成的消息并写入消费位置。"""
        reader, owner, normalized_ids = _claim_identity(
            reader_agent_id,
            owner_id,
            event_ids,
        )
        acknowledged = tuple(
            event_id
            for event_id in normalized_ids
            if self._claims.get(event_id) == (reader, owner)
        )
        if not acknowledged:
            return ()

        self._consumed.setdefault(reader, set()).update(acknowledged)
        for event_id in acknowledged:
            self._claims.pop(event_id, None)
        return acknowledged

    def release_claim(
        self,
        reader_agent_id: str,
        owner_id: str,
        event_ids: typing.Collection[str],
    ) -> tuple[str, ...]:
        """释放指定投递者尚未确认的消息。"""
        reader, owner, normalized_ids = _claim_identity(
            reader_agent_id,
            owner_id,
            event_ids,
        )
        released = tuple(
            event_id
            for event_id in normalized_ids
            if self._claims.get(event_id) == (reader, owner)
        )
        for event_id in released:
            self._claims.pop(event_id, None)
        return released

    def _select_messages(
        self,
        reader: str,
        *,
        limit: int,
        max_chars: int,
    ) -> tuple[AgentMailboxEvent, ...]:
        """在不改变消费位置的前提下选择可投递消息。"""
        consumed = self._consumed.setdefault(reader, set())
        selected: list[AgentMailboxEvent] = []

        selected_chars = 0
        context_budget = max_chars - 128

        for event in self._events:
            if (
                event.event_id in consumed
                or event.event_id in self._claims
                or event.kind != "message"
                or event.recipient_agent_id != reader
            ):
                continue
            event_chars = len(json.dumps(
                _context_message(event),
                ensure_ascii=False,
                separators=(",", ":"),
            )) + 1
            if selected and selected_chars + event_chars > context_budget:
                break
            selected.append(event)
            selected_chars += event_chars
            if len(selected) >= limit:
                break
        return tuple(selected)

    def _trim(self) -> None:
        """丢弃超出容量的最旧事件及其消费标记。"""
        while len(self._events) > self._capacity:
            removed = self._events.popleft()
            self._claims.pop(removed.event_id, None)
            for consumed in self._consumed.values():
                consumed.discard(removed.event_id)


def format_mailbox_context(events: typing.Iterable[AgentMailboxEvent]) -> str:
    """把邮箱消息转换为可注入轮次的结构化上下文。"""
    messages = [
        _context_message(event)
        for event in events
        if event.kind == "message"
    ]
    if not messages:
        return ""
    payload = json.dumps(
        {"messages": messages},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"Agent mailbox messages received before this turn:\n{payload}"


def _context_message(event: AgentMailboxEvent) -> dict[str, str]:
    """返回轮次上下文使用的最小消息载荷。"""
    return {
        "event_id": event.event_id,
        "from_agent_id": event.source_agent_id,
        "from_task_path": event.source_task_path,
        "message": event.message,
    }


def _require_positive_limit(value: int, field: str) -> None:
    """校验邮箱读取使用的正整数上限。"""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")


def _claim_identity(
    reader_agent_id: str,
    owner_id: str,
    event_ids: typing.Collection[str],
) -> tuple[str, str, tuple[str, ...]]:
    """校验并规范化一项邮箱 claim 操作。"""
    if isinstance(event_ids, (str, bytes)):
        raise TypeError("mailbox event ids must be a collection")

    reader = str(reader_agent_id or "").strip()
    owner = str(owner_id or "").strip()

    normalized_ids = tuple(dict.fromkeys(
        str(event_id or "").strip()
        for event_id in event_ids
    ))
    if not reader or not owner or not normalized_ids or "" in normalized_ids:
        raise ValueError("mailbox reader, owner, and event ids are required")
    return reader, owner, normalized_ids


if __name__ == '__main__':
    pass
