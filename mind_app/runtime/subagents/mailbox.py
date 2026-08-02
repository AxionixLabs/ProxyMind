# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import time
import typing
from collections import deque
from dataclasses import dataclass
from mind_nova.identifiers import short_uid
from mind_app.runtime.execution import AgentContext

AgentMailboxEventKind = typing.Literal["message", "queue", "status"]

MAX_MAILBOX_EVENTS        = 1000
MAX_MAILBOX_UPDATES       = 50
MAX_AGENT_MESSAGE_CHARS   = 16_000
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
            if event.event_id in consumed or event.source_agent_id not in sources:
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
        selected: list[AgentMailboxEvent] = []
        selected_chars = 0
        context_budget = max_chars - 128
        for event in self._events:
            if (
                event.event_id in consumed
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

        consumed.update(event.event_id for event in selected)
        return tuple(selected)

    def _trim(self) -> None:
        """丢弃超出容量的最旧事件及其消费标记。"""
        while len(self._events) > self._capacity:
            removed = self._events.popleft()
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


if __name__ == '__main__':
    pass
