# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Iterable,
    Mapping,
)

from agent.domain.policies import PermissionSettings
from agent.ports.hooks import HookScopeProviderPort
from agent.ports.transcript import TranscriptFactory

if typing.TYPE_CHECKING:
    from agent.application.turns.compact_result import CompactEvent

__all__ = (
    "CompactProgress",
    "CompactionClientPort",
    "CompactionSessionPort",
)


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

    def snapshot(self) -> Mapping[str, str]:
        """返回当前会话的稳定 cid/sid 快照。"""
        ...

    def transcript_path_for_session(self, sid: str) -> str:
        """返回指定会话的 Transcript 路径。"""
        ...

    @property
    def hook_scope_provider(self) -> HookScopeProviderPort:
        """返回压缩生命周期使用的 Hook 作用域提供器。"""
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


if __name__ == '__main__':
    pass
