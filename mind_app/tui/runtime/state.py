# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import copy
import typing
import contextlib
import contextvars
from dataclasses import dataclass
from ..core.activity import ActivityLease


@dataclass(frozen=True, slots=True)
class ProcessCompletion(object):
    """保存尚未由用户确认的后台进程完成状态。"""
    snapshot: dict[str, typing.Any]
    label: str


class ProcessCompletionStore(object):
    """按完成顺序保存进程快照，并隔离调用方的后续修改。"""

    def __init__(self) -> None:
        self._items: dict[str, ProcessCompletion] = {}

    def retain(
        self,
        snapshot: dict[str, typing.Any],
        *,
        label: str,
    ) -> bool:
        """保存有效会话快照，并返回是否发生了状态变更。"""
        session_id = str(snapshot.get("session_id") or "").strip()
        if not session_id:
            return False

        self._items.pop(session_id, None)
        self._items[session_id] = ProcessCompletion(
            snapshot=copy.deepcopy(snapshot),
            label=str(label or "").strip(),
        )
        return True

    def snapshots(self) -> tuple[dict[str, typing.Any], ...]:
        """返回与内部状态隔离的全部完成快照。"""
        return tuple(
            copy.deepcopy(item.snapshot)
            for item in self._items.values()
        )

    def acknowledge(self, session_id: typing.Any) -> bool:
        """移除指定会话快照，并返回是否命中。"""
        sid = str(session_id or "").strip()
        return bool(sid and self._items.pop(sid, None) is not None)

    def latest_label(self, fallback: str) -> str:
        """返回最近完成项标签；没有完成项时返回运行中标签。"""
        completion = next(reversed(self._items.values()), None)
        return completion.label if completion is not None else fallback

    def clear(self) -> None:
        """清空全部尚未确认的完成状态。"""
        self._items.clear()


@dataclass
class ActivityHandoff(object):
    """保存当前任务结果接管活动区域的状态。"""
    lease: ActivityLease | None
    deferred: bool
    consumed: bool = False


class ActivityHandoffState(object):
    """隔离每个异步上下文中的活动区域交接状态。"""

    def __init__(self) -> None:
        self._current = contextvars.ContextVar[ActivityHandoff | None](
            "tui_activity_handoff",
            default=None,
        )

    @contextlib.contextmanager
    def bind(
        self,
        lease: ActivityLease | None,
        *,
        deferred: bool,
    ) -> contextlib.AbstractContextManager[ActivityHandoff]:
        """在当前异步上下文绑定一次性活动区域交接。"""
        handoff = ActivityHandoff(lease=lease, deferred=deferred)
        token = self._current.set(handoff)
        try:
            yield handoff
        finally:
            self._current.reset(token)

    def consume(
        self,
        *,
        deferred: bool,
        freeze: typing.Callable[[ActivityLease], None],
    ) -> ActivityLease | None:
        """消费当前交接，并在延迟提交前冻结活动区域。"""
        handoff = self._current.get()
        if handoff is None or handoff.consumed:
            return None

        lease = handoff.lease
        if lease is None:
            handoff.consumed = True
            return None

        if deferred and not handoff.deferred:
            freeze(lease)
            handoff.deferred = True

        handoff.consumed = True
        return lease


@dataclass
class CommandLayoutHandoff(object):
    """保存命令结果接管活动区域前的业务同步状态。"""
    consumed: bool = False


class CommandLayoutState(object):
    """拥有命令布局 handoff，并保持创建任务时的上下文隔离。"""

    def __init__(self) -> None:
        self._current = contextvars.ContextVar[CommandLayoutHandoff | None](
            "tui_command_layout",
            default=None,
        )

    @property
    def pending(self) -> bool:
        """返回当前上下文是否仍等待首个业务结果。"""
        handoff = self._current.get()
        return handoff is not None and not handoff.consumed

    def begin(self) -> None:
        """在当前上下文开始一次新的命令布局交接。"""
        self.cancel()
        self._current.set(CommandLayoutHandoff())

    def consume(self) -> bool:
        """消费当前上下文的命令布局交接。"""
        handoff = self._current.get()
        if handoff is None or handoff.consumed:
            return False
        handoff.consumed = True
        return True

    def cancel(self) -> None:
        """清除当前异步上下文中的命令布局交接。"""
        self._current.set(None)


if __name__ == '__main__':
    pass
