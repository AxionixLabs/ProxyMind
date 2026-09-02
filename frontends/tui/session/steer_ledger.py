# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import enum
from dataclasses import dataclass

from ..core.queued import TuiSubmission


class SteerState(enum.Enum):
    LOCAL = "local"
    SENT = "sent"
    COMMITTED = "committed"


class LedgerState(enum.Enum):
    ACTIVE = "active"
    SETTLED = "settled"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class SteerResolution(object):
    """描述关闭输入账本后需要执行的本地处理。"""
    retry: tuple[TuiSubmission, ...]
    uncertain: tuple[TuiSubmission, ...]
    resolved_ids: tuple[str, ...]


class PendingSteerLedger(object):
    """按提交顺序记录活动轮次输入的唯一状态。"""

    def __init__(self) -> None:
        self._state = LedgerState.ACTIVE
        self._items: dict[str, tuple[TuiSubmission, SteerState]] = {}

    @property
    def settled(self) -> bool:
        """返回当前逻辑轮次是否已经结算。"""
        return self._state is LedgerState.SETTLED

    def add(self, submission: TuiSubmission) -> None:
        """记录一条尚未发送的本地输入。"""
        if self._state is LedgerState.CLOSED:
            raise RuntimeError("cannot add input to a closed ledger")
        self._items.setdefault(
            submission.client_message_id,
            (submission, SteerState.LOCAL),
        )

    def next_local(self) -> TuiSubmission | None:
        """返回最早一条尚未发送的输入。"""
        for submission, state in self._items.values():
            if state is SteerState.LOCAL:
                return submission
        return None

    def mark_sent(self, client_message_id: str) -> None:
        """把输入标记为已经开始远端提交。"""
        item = self._items.get(client_message_id)
        if item is None:
            return None
        submission, state = item
        if state is SteerState.LOCAL:
            self._items[client_message_id] = (submission, SteerState.SENT)

    def release(self, client_message_id: str) -> TuiSubmission | None:
        """移除并返回一条已经明确归属的输入。"""
        item = self._items.pop(client_message_id, None)
        return item[0] if item is not None else None

    def commit(self, client_message_id: str) -> TuiSubmission | None:
        """确认输入已写入当前轮次并返回其本地副本。"""
        item = self._items.get(client_message_id)
        if item is None:
            return None
        submission, state = item
        if state is SteerState.COMMITTED:
            return None
        self._items[client_message_id] = (
            submission,
            SteerState.COMMITTED,
        )
        return submission

    def settle(self) -> None:
        """标记当前逻辑轮次已经完成结算。"""
        if self._state is not LedgerState.CLOSED:
            self._state = LedgerState.SETTLED

    def sent_ids(self) -> tuple[str, ...]:
        """返回需要远端对账的已发送输入标识。"""
        return tuple(
            client_message_id
            for client_message_id, (_, state) in self._items.items()
            if state is SteerState.SENT
        )

    def advance(self) -> SteerResolution:
        """进入续跑轮次并保留无法安全迁移的输入。"""
        resolved = tuple(
            (client_message_id, submission, state)
            for client_message_id, (submission, state) in self._items.items()
            if state is not SteerState.LOCAL
        )
        for client_message_id, _, _ in resolved:
            self._items.pop(client_message_id, None)
        uncertain = () if self.settled else tuple(
            submission
            for _, submission, state in resolved
            if state is SteerState.SENT
        )
        self._state = LedgerState.ACTIVE
        return SteerResolution(
            retry=(),
            uncertain=uncertain,
            resolved_ids=tuple(
                client_message_id
                for client_message_id, _, _ in resolved
            ),
        )

    def close(
        self,
        *,
        committed_ids: tuple[str, ...] = (),
        retry_ids: tuple[str, ...] = ()
    ) -> SteerResolution:
        """关闭账本并按最终归属返回本地处理结果。"""
        committed = set(committed_ids)
        retry = set(retry_ids)

        retry_items: list[TuiSubmission] = []
        uncertain_items: list[TuiSubmission] = []

        resolved_ids = tuple(self._items)

        for client_message_id, (submission, state) in self._items.items():
            if state is SteerState.LOCAL or client_message_id in retry:
                retry_items.append(submission)
            elif (
                state is not SteerState.COMMITTED
                and client_message_id not in committed
                and not self.settled
            ):
                uncertain_items.append(submission)

        self._items.clear()
        self._state = LedgerState.CLOSED
        return SteerResolution(
            retry=tuple(retry_items),
            uncertain=tuple(uncertain_items),
            resolved_ids=resolved_ids,
        )


if __name__ == '__main__':
    pass
