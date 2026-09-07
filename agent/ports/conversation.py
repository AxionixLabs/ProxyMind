# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from collections.abc import Collection
from pathlib import Path

from agent.domain.hooks import SessionEndReason
from agent.domain.transcripts import TranscriptEntry
from agent.protocol import AssistantReplySnapshot

__all__ = (
    "ConversationHistoryPort",
    "RootConversationPort",
)


class ConversationHistoryPort(typing.Protocol):
    """定义入口查询和维护本地会话游标所需的边界。"""

    @property
    def ttl_ms(self) -> int:
        """返回历史游标的保留时间。"""
        ...

    @property
    def max_items(self) -> int:
        """返回历史游标的容量上限。"""
        ...

    def touch(
        self,
        metadata: dict[str, str],
        *,
        workspace: str,
        title: str = "",
        source: str,
    ) -> None:
        """更新指定会话的本地历史游标。"""
        ...

    def recent(
        self,
        *,
        workspace: str | Path | None = None,
        sources: Collection[str] | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, typing.Any]]:
        """返回最近的可恢复会话。"""
        ...

    def find(
        self,
        session_id: str,
        *,
        workspace: str | Path | None = None,
        sources: Collection[str] | None = None,
        status: str | None = None,
    ) -> dict[str, typing.Any] | None:
        """按标识查找可恢复会话。"""
        ...

    def read_transcript(self, session_id: str) -> tuple[TranscriptEntry, ...]:
        """读取指定会话的结构化 Transcript。"""
        ...

    def prepare_fork(
        self,
        cid: str,
        sid: str,
        before_turn_id: str = "",
    ) -> str:
        """返回持久化的分支幂等请求标识。"""
        ...

    def clear_fork(
        self,
        cid: str,
        sid: str,
        request_id: str,
        before_turn_id: str = "",
    ) -> None:
        """清除已经收束的分支幂等请求。"""
        ...

    def unarchive(self, *, cid: str, sid: str) -> dict[str, typing.Any]:
        """把指定会话恢复到 active 集合。"""
        ...

    def archive(self, *, cid: str, sid: str) -> dict[str, typing.Any]:
        """把指定会话迁移到 archived 集合。"""
        ...


class RootConversationPort(typing.Protocol):
    """定义入口观察和控制根会话生命周期所需的公共边界。"""

    @property
    def cid(self) -> str | None:
        """返回当前 conversation ID。"""
        ...

    @property
    def sid(self) -> str | None:
        """返回当前 session ID。"""
        ...

    @property
    def turn_count(self) -> int:
        """返回当前根会话已经开始的轮次数。"""
        ...

    @property
    def session_bound(self) -> bool:
        """返回当前会话是否已经绑定稳定坐标。"""
        ...

    @property
    def fork_source_available(self) -> bool:
        """返回当前会话是否存在可分支输入。"""
        ...

    @property
    def history(self) -> ConversationHistoryPort:
        """返回根会话绑定的历史查询边界。"""
        ...

    def snapshot(self) -> dict[str, str]:
        """返回当前会话稳定坐标。"""
        ...

    async def reset(
        self,
        *,
        reason: str = "manual",
        source: str = "reset",
        title: str = "",
    ) -> dict[str, str]:
        """结束当前会话并创建新坐标。"""
        ...

    async def resume(
        self,
        record: dict[str, typing.Any],
        *,
        source: str = "resume",
    ) -> dict[str, str] | None:
        """恢复指定历史会话。"""
        ...

    async def bind(
        self,
        cid: str,
        sid: str,
        *,
        source: str = "bind",
    ) -> dict[str, str] | None:
        """绑定指定远端会话坐标。"""
        ...

    async def end(self, *, reason: SessionEndReason) -> None:
        """结束当前根会话生命周期。"""
        ...

    async def archive_current(self) -> dict[str, typing.Any]:
        """归档并结束当前根会话。"""
        ...

    async def archive(self, cid: str, sid: str) -> dict[str, typing.Any]:
        """归档指定的非当前会话。"""
        ...

    def assistant_reply_snapshot(self) -> AssistantReplySnapshot | None:
        """返回最近一次完整 assistant 回复的稳定快照。"""
        ...


if __name__ == '__main__':
    pass
