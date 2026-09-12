# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from collections.abc import (
    Callable,
    Collection,
)
from pathlib import Path

from agent.domain.hooks import SessionEndReason
from agent.domain.transcripts import TranscriptEntry
from agent.ports.workspace import WorkspaceChangePort
from agent.protocol import AssistantReplySnapshot
from agent.protocol.context_usage import ContextUsageRecord

if typing.TYPE_CHECKING:
    from agent.application.views.context_usage import ContextUsageView

__all__ = (
    "ContextUsageRecovery",
    "ContextUsageRecoveryError",
    "ContextUsageFeed",
    "ConversationHistoryPort",
    "RootConversationPort",
)


class ContextUsageRecoveryError(RuntimeError):
    """表示远端用量恢复未取得完整可信快照，不携带报告鉴权信息。"""


class ContextUsageRecovery(typing.Protocol):
    """读取远端会话的完整用量事实；实现方拥有单次请求并负责关闭传输。

    返回空值表示权威未知；失败抛出 ContextUsageRecoveryError，不推进聊天确认游标。
    """

    async def load(self, cid: str, sid: str) -> ContextUsageRecord | None:
        """完成有限回放读取后返回最新快照，不创建模型请求。"""
        ...


class ContextUsageFeed(typing.Protocol):
    """提供根会话用量的只读视图；订阅者须在展示生命周期结束时解除订阅。"""

    @property
    def view(self) -> "ContextUsageView":
        """读取空闲期间仍保留的当前投影。"""
        ...

    def subscribe(
        self, listener: Callable[["ContextUsageView"], None],
    ) -> Callable[[], None]:
        """立即发送当前视图，并返回幂等取消订阅函数。"""
        ...


class ConversationHistoryPort(typing.Protocol):
    """定义入口查询和维护本地会话游标所需的边界。"""

    def load_context_usage(self, cid: str, sid: str) -> ContextUsageRecord | None:
        """读取随历史游标保留的已确认用量缓存，不补算缺失数据。"""
        ...

    def save_context_usage(self, record: ContextUsageRecord) -> bool:
        """单调缓存完整事实，返回是否已持久化；不推进远端确认游标。"""
        ...

    def discard_context_usage_prefix(self, cid: str, sid: str, event_seq: int) -> None:
        """删除指定历史裁剪水位以内的缓存；实现方必须保留更新记录。"""
        ...

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

    def update_title(
        self,
        cid: str,
        sid: str,
        title: str,
    ) -> bool:
        """用服务端权威标题更新已有的本地会话游标。"""
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
    def context_usage(self) -> ContextUsageFeed:
        """提供随根会话存在的只读展示订阅，调用方负责取消订阅。"""
        ...

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

    def update_title(
        self,
        cid: str,
        sid: str,
        title: str,
        *,
        source: str = "remote",
    ) -> bool:
        """更新当前会话标题并返回事件是否属于活动会话。"""
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
        workspace_change: WorkspaceChangePort | None = None,
    ) -> dict[str, str] | None:
        """恢复指定历史会话。"""
        ...

    async def bind(
        self,
        cid: str,
        sid: str,
        *,
        source: str = "bind",
        workspace_change: WorkspaceChangePort | None = None,
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
