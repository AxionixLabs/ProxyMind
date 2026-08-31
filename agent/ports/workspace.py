# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import typing
from .capabilities import ProcessCapability

__all__ = (
    "CodingFactory",
    "CodingRuntime",
    "ExecutionPolicy",
    "ExecutionPolicyFactory",
    "WorkspaceRoot",
    "WorkspaceRuntime",
    "WorkspaceRuntimeFactory",
)

WorkspaceRoot: typing.TypeAlias = str | os.PathLike[str]


class CodingRuntime(typing.Protocol):
    """定义工作区编码能力的资源生命周期契约。"""

    user_shell: object

    async def close(self) -> None:
        """释放编码能力持有的全部资源。"""
        ...


class ExecutionPolicy(typing.Protocol):
    """定义绑定工作区的执行策略标记契约。"""

    ...


class CodingFactory(typing.Protocol):
    """定义组合根创建工作区编码能力的工厂契约。"""

    def __call__(
        self,
        *,
        root: WorkspaceRoot,
        application_layout: object | None,
        process_capability: ProcessCapability | None = None,
    ) -> CodingRuntime:
        ...


class ExecutionPolicyFactory(typing.Protocol):
    """定义组合根创建工作区执行策略的工厂契约。"""

    def __call__(self, *, workspace_root: WorkspaceRoot) -> ExecutionPolicy:
        ...


class WorkspaceRuntime(typing.Protocol):
    """定义 Controller 消费的工作区资源生命周期边界。"""

    coding: CodingRuntime
    execution_policy: ExecutionPolicy
    user_shell: object

    def replace(self, workspace_root: WorkspaceRoot) -> None:
        """切换工作区并安排旧编码资源回收。"""
        ...

    async def close(self) -> None:
        """关闭当前工作区及其共享进程能力。"""
        ...


class WorkspaceRuntimeFactory(typing.Protocol):
    """定义创建工作区运行时的组合端口。"""

    def __call__(
        self,
        workspace_root: WorkspaceRoot,
        *,
        application_layout: object | None = None,
        process_capability: ProcessCapability | None = None,
    ) -> WorkspaceRuntime:
        ...


if __name__ == '__main__':
    pass
