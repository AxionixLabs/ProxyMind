# -*- coding: utf-8 -*-

import typing
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Iterable,
    Mapping,
)

from agent.domain.policies import PermissionSettings
from agent.ports.hooks import HookExecutionScopePort
from agent.ports.transcript import TranscriptFactory

if typing.TYPE_CHECKING:
    from agent.application.hooks.context import HookExecutionContext
    from agent.application.turns.compact_result import CompactEvent


CompactProgress: typing.TypeAlias = typing.Callable[[str], None]
CleanupValue = typing.TypeVar("CleanupValue")


class CompactionClientPort(typing.Protocol):
    """定义会话压缩协议适配器返回中立事件流的边界。"""

    def stream(
        self,
        *,
        cid: str,
        sid: str,
        pref_config: dict[str, typing.Any],
    ) -> AsyncIterator["CompactEvent"]:
        """提交压缩请求并返回归一化进度和终态事件。"""
        ...


class CompactionSessionPort(typing.Protocol):
    """定义压缩用例读取当前会话状态和生命周期所需的最小端口。"""

    @property
    def workspace_root(self) -> str:
        """返回当前会话绑定的工作区。"""
        ...

    @property
    def permissions(self) -> PermissionSettings:
        """返回当前会话权限快照。"""
        ...

    @property
    def transcript_factory(self) -> TranscriptFactory:
        """返回会话 Transcript writer 工厂。"""
        ...

    def conversation_identity(self) -> Mapping[str, str]:
        """返回当前会话的稳定 cid/sid 快照。"""
        ...

    def transcript_path_for_session(self, sid: str) -> str:
        """返回指定会话的 Transcript 路径。"""
        ...

    def hook_scope(
        self,
        context: "HookExecutionContext",
    ) -> HookExecutionScopePort:
        """为压缩生命周期创建固定 Hook 作用域。"""
        ...

    async def await_cleanup(
        self,
        awaitable: Awaitable[CleanupValue],
    ) -> CleanupValue:
        """在取消态下等待压缩清理操作收束。"""
        ...

    def queue_turn_context(self, contexts: Iterable[str]) -> None:
        """把 Hook 产生的后续上下文排入下一轮。"""
        ...


__all__ = (
    "CompactProgress",
    "CompactionClientPort",
    "CompactionSessionPort",
)
