# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

if typing.TYPE_CHECKING:
    from mind_app.controller import Mind
    from mind_app.runtime.mcp.external import ExternalMcpRuntime


ExternalMcpRuntimeFactory = typing.Callable[["Mind"], "ExternalMcpRuntime"]


class ExternalMcpRuntimeOwner(object):
    """持有外部 MCP 运行时，并管理启动、重启和最终释放语义。"""

    def __init__(
        self,
        controller: "Mind",
        *,
        runtime_factory: ExternalMcpRuntimeFactory | None = None,
    ) -> None:
        """绑定外部 MCP 运行时所需控制器和可选实例工厂。"""
        self._controller = controller
        self._runtime_factory = runtime_factory
        self._runtime: ExternalMcpRuntime | None = None

    @property
    def current(self) -> "ExternalMcpRuntime | None":
        """返回当前持有的外部 MCP 运行时。"""
        return self._runtime

    async def start(
        self,
        *,
        include_disabled: bool = False,
        defer_activity_stop: bool = False,
    ) -> None:
        """启动或复用当前外部 MCP 运行时。"""
        runtime = self._runtime
        if runtime is None:
            runtime = self._create_runtime()
            self._runtime = runtime
        await runtime.start(
            include_disabled=include_disabled,
            defer_activity_stop=defer_activity_stop,
        )

    async def restart(
        self,
        *,
        include_disabled: bool = False,
        defer_activity_stop: bool = False,
    ) -> None:
        """重启当前外部 MCP 运行时，尚未创建时先建立实例。"""
        runtime = self._runtime
        if runtime is None:
            runtime = self._create_runtime()
            self._runtime = runtime
        await runtime.restart(
            include_disabled=include_disabled,
            defer_activity_stop=defer_activity_stop,
        )

    async def close(self) -> None:
        """解除实例所有权，并在取消态下等待运行时完成清理。"""
        runtime = self._runtime
        self._runtime = None
        if runtime is not None:
            await self._controller.await_cleanup(runtime.stop())

    def _create_runtime(self) -> "ExternalMcpRuntime":
        """使用已绑定工厂创建外部 MCP 运行时。"""
        factory = self._runtime_factory
        if factory is None:
            from mind_app.runtime.mcp.external import ExternalMcpRuntime

            factory = ExternalMcpRuntime
        return factory(self._controller)


if __name__ == '__main__':
    pass
