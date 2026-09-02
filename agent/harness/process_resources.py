# -*- coding: utf-8 -*-

import asyncio
import typing
from collections.abc import (
    Awaitable,
    Callable,
)
from dataclasses import dataclass

AsyncClose: typing.TypeAlias = Callable[[], Awaitable[None]]
SyncClose: typing.TypeAlias = Callable[[], None]
CloseFailureObserver: typing.TypeAlias = Callable[[str, BaseException], None]


@dataclass(frozen=True, slots=True)
class _CloseStep:
    name: str
    close: AsyncClose


class ProcessResourceOwner:
    """单一持有进程共享资源的关闭顺序和重试进度。

    所有资源由组合根创建并注入。本对象不创建具体实现；关闭失败时保留当前步骤，
    后续调用从失败点继续，已经完成的前置步骤不会重复执行。
    """

    def __init__(
        self,
        *,
        close_subscription: AsyncClose,
        cancel_service_startup: AsyncClose,
        shutdown_subagents: AsyncClose,
        close_approvals: AsyncClose,
        clear_command_hooks: SyncClose,
        close_hooks: AsyncClose,
        close_javascript: AsyncClose,
        close_workspace: AsyncClose,
        close_execution: AsyncClose,
        close_service: AsyncClose,
        observe_failure: CloseFailureObserver,
    ) -> None:
        """冻结资源关闭顺序。"""

        async def clear_command_hook_sessions() -> None:
            clear_command_hooks()

        self._steps = (
            _CloseStep("subscription", close_subscription),
            _CloseStep("service_startup", cancel_service_startup),
            _CloseStep("subagents", shutdown_subagents),
            _CloseStep("approvals", close_approvals),
            _CloseStep("command_hooks", clear_command_hook_sessions),
            _CloseStep("hooks", close_hooks),
            _CloseStep("javascript", close_javascript),
            _CloseStep("workspace", close_workspace),
            _CloseStep("execution", close_execution),
            _CloseStep("service", close_service),
        )
        self._next_step = 0
        self._close_lock = asyncio.Lock()
        self._observe_failure = observe_failure

    async def close(self) -> None:
        """串行关闭资源，并在失败时保存可重试进度。"""
        async with self._close_lock:
            if self._next_step >= len(self._steps):
                return None

            while self._next_step < len(self._steps):
                step = self._steps[self._next_step]
                try:
                    await step.close()
                except BaseException as error:
                    self._observe_failure(step.name, error)
                    raise
                self._next_step += 1


__all__ = ("ProcessResourceOwner",)
