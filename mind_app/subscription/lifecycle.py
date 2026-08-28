# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

if typing.TYPE_CHECKING:
    from mind_app.controller import Mind
    from mind_app.subscription.runtime import AgentRuntime


SubscriptionRuntimeFactory = typing.Callable[["Mind"], "AgentRuntime"]


class SubscriptionRuntimeOwner(object):
    """持有进程内订阅监听器，并管理暂停和最终释放语义。"""

    def __init__(
        self,
        controller: "Mind",
        *,
        runtime_factory: SubscriptionRuntimeFactory | None = None,
    ) -> None:
        """绑定监听器所需控制器和可选实例工厂。"""
        self._controller = controller
        self._runtime_factory = runtime_factory
        self._runtime: AgentRuntime | None = None

    @property
    def current(self) -> "AgentRuntime | None":
        """返回当前进程持有的监听器实例。"""
        return self._runtime

    def start(self) -> "AgentRuntime":
        """启动或复用当前进程的监听器实例。"""
        runtime = self._runtime
        if runtime is None:
            factory = self._runtime_factory
            if factory is None:
                from mind_app.subscription.runtime import AgentRuntime

                factory = AgentRuntime
            runtime = factory(self._controller)
            self._runtime = runtime
        runtime.start_background()
        return runtime

    async def pause(self) -> None:
        """停止监听器传输，同时保留进程内收件箱。"""
        runtime = self._runtime
        if runtime is not None:
            await runtime.stop()

    async def close(self) -> None:
        """解除监听器回调并释放当前实例。"""
        runtime = self._runtime
        self._runtime = None
        if runtime is None:
            return None

        runtime.bind_inbox_changed(None)
        await runtime.shutdown()


if __name__ == '__main__':
    pass
