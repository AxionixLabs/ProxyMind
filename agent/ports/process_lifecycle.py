# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import typing
from collections.abc import Awaitable

__all__ = ("ProcessLifecyclePort",)


CleanupResult = typing.TypeVar("CleanupResult")


@typing.runtime_checkable
class ProcessLifecyclePort(typing.Protocol):
    """定义进程级停止、退出结果和取消态清理契约。"""

    @property
    def stop_event(self) -> asyncio.Event:
        """返回进程级协作停止信号。"""
        ...

    @property
    def exit_code(self) -> int:
        """返回当前进程退出码。"""
        ...

    def set_exit_code(self, value: int) -> None:
        """更新进程退出码。"""
        ...

    def request_stop(self, *, exit_code: int | None = None) -> None:
        """设置协作停止信号，并可原子更新退出码。"""
        ...

    async def await_cleanup(
        self,
        awaitable: Awaitable[CleanupResult],
    ) -> CleanupResult:
        """在调用方取消时等待清理操作收敛。"""
        ...


if __name__ == '__main__':
    pass
