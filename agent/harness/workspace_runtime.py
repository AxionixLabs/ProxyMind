# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import contextlib
import os
import typing

from agent.ports import ProcessCapability

WorkspaceRoot: typing.TypeAlias = str | os.PathLike[str]


class CodingRuntime(typing.Protocol):
    """描述工作区编码能力在生命周期管理中需要的最小接口。"""

    user_shell: object

    async def close(self) -> None:
        """释放编码能力持有的全部资源。"""
        ...


class ExecutionPolicy(typing.Protocol):
    """标记由组合根创建并绑定工作区的执行策略实例。"""

    ...


class CodingFactory(typing.Protocol):
    """创建绑定工作区的编码能力，具体实现由组合根提供。"""

    def __call__(
        self,
        *,
        root: WorkspaceRoot,
        application_layout: object | None,
        process_capability: ProcessCapability | None = None,
    ) -> CodingRuntime:
        ...


class ExecutionPolicyFactory(typing.Protocol):
    """创建绑定工作区的执行策略，具体实现由组合根提供。"""

    def __call__(self, *, workspace_root: WorkspaceRoot) -> ExecutionPolicy:
        ...


class WorkspaceRuntime(typing.Protocol):
    """描述 Controller 消费的工作区资源生命周期边界。"""

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
    """创建工作区运行时，隔离 Harness 与具体本机实现。"""

    def __call__(
        self,
        workspace_root: WorkspaceRoot,
        *,
        application_layout: object | None = None,
        process_capability: ProcessCapability | None = None,
    ) -> WorkspaceRuntime:
        ...


class WorkspaceRuntimeOwner:
    """持有当前工作区的编码、Shell 与执行策略资源。"""

    def __init__(
        self,
        workspace_root: WorkspaceRoot,
        *,
        application_layout: object | None,
        coding_factory: CodingFactory,
        execution_policy_factory: ExecutionPolicyFactory,
        process_capability: ProcessCapability | None = None,
    ) -> None:
        """为初始工作区创建运行时资源并绑定实例工厂。"""
        self._application_layout = application_layout
        self._coding_factory = coding_factory
        self._execution_policy_factory = execution_policy_factory
        self._process_capability = process_capability
        self._retired_codings: set[CodingRuntime] = set()
        self._close_tasks: set[asyncio.Task[None]] = set()
        self._closed = False

        self.execution_policy = self._create_execution_policy(workspace_root)
        self.coding = self._create_coding(workspace_root)
        self.user_shell = self.coding.user_shell

    def replace(self, workspace_root: WorkspaceRoot) -> None:
        """替换当前工作区资源，并异步回收旧编码实例。"""
        if self._closed:
            raise RuntimeError("workspace runtime is closed")

        execution_policy = self._create_execution_policy(workspace_root)
        coding = self._create_coding(workspace_root)
        previous_coding = self.coding

        self.execution_policy = execution_policy
        self.coding = coding
        self.user_shell = coding.user_shell
        self._retire(previous_coding)

    async def close(self) -> None:
        """关闭当前实例并等待已退役实例完成回收。"""
        if self._closed:
            return None

        with contextlib.suppress(Exception):
            await self.coding.close()

        retired = tuple(self._retired_codings)
        self._retired_codings.clear()
        if retired:
            await asyncio.gather(
                *(coding.close() for coding in retired),
                return_exceptions=True,
            )

        close_tasks = tuple(self._close_tasks)
        self._close_tasks.clear()
        if close_tasks:
            await asyncio.gather(*close_tasks, return_exceptions=True)

        if self._process_capability is not None:
            await self._process_capability.aclose()

        self._closed = True

    def _create_coding(self, workspace_root: WorkspaceRoot) -> CodingRuntime:
        """创建绑定指定工作区的编码运行时。"""
        if self._process_capability is None:
            return self._coding_factory(
                root=workspace_root,
                application_layout=self._application_layout,
            )
        return self._coding_factory(
            root=workspace_root,
            application_layout=self._application_layout,
            process_capability=self._process_capability,
        )

    def _create_execution_policy(
        self,
        workspace_root: WorkspaceRoot,
    ) -> ExecutionPolicy:
        """创建绑定指定工作区的执行策略管理器。"""
        return self._execution_policy_factory(workspace_root=workspace_root)

    def _retire(self, coding: CodingRuntime) -> None:
        """将旧编码实例加入当前或最终回收流程。"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._retired_codings.add(coding)
            return None

        task = loop.create_task(
            coding.close(),
            name="coding workspace close",
        )
        self._close_tasks.add(task)
        task.add_done_callback(self._close_task_done)

    def _close_task_done(self, task: asyncio.Task[None]) -> None:
        """回收工作区替换时启动的关闭任务。"""
        self._close_tasks.discard(task)
        if not task.cancelled():
            task.exception()
