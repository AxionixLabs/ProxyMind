# -*- coding: utf-8 -*-

import typing
from dataclasses import dataclass

from agent.domain.agents import (
    AgentStatus,
    AgentSubmission,
)
from .agent_thread import AgentThreadContext
from .execution import AgentContext

if typing.TYPE_CHECKING:
    from agent.stores.agent_mailbox import AgentMailboxEvent


@dataclass(frozen=True, slots=True)
class AgentSnapshot:
    """保存执行主体当前状态的不可变 application 视图。"""

    thread: AgentThreadContext
    status: AgentStatus
    submission: AgentSubmission | None = None
    submission_id: str = ""
    turn_count: int = 0
    queued_count: int = 0
    result: typing.Any = None
    error: str = ""

    @property
    def agent_id(self) -> str:
        """返回执行主体标识。"""
        return self.thread.agent.agent_id

    @property
    def context(self) -> AgentContext:
        """返回执行主体身份。"""
        return self.thread.agent


@dataclass(frozen=True, slots=True)
class AgentWaitResult:
    """保存等待操作得到的终态快照。"""

    snapshots: tuple[AgentSnapshot, ...] = ()
    timed_out: bool = False


@dataclass(frozen=True, slots=True)
class AgentMailboxWaitResult:
    """保存动态等待得到的事件和目标快照。"""

    events: tuple["AgentMailboxEvent", ...] = ()
    snapshots: tuple[AgentSnapshot, ...] = ()
    timed_out: bool = False


__all__ = (
    "AgentMailboxWaitResult",
    "AgentSnapshot",
    "AgentWaitResult",
)
