# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import typing
from dataclasses import dataclass

from agent.domain.execution_policy import (
    ExecutionPolicyAmendment,
    ExecutionPolicyRequirement,
)
from agent.domain.policies import NetworkAccess
from .capabilities import ProcessCapability
from .interactive_process import InteractiveProcessCapability
from .media import ImageReaderPort
from .network import (
    NetworkBlockedHandlerFactory,
    NetworkPolicyPort,
)
from .patching import WorkspacePatchPort
from .process_tools import (
    UserShellPort,
    WorkspaceProcessPort,
)

__all__ = (
    "CodingFactory",
    "CodingRuntime",
    "ExecutionPolicy",
    "ExecutionPolicyAmendment",
    "ExecutionPolicyFactory",
    "ExecutionPolicyRequirement",
    "PatchPreviewPort",
    "WorkspaceChangePort",
    "WorkspaceCodingPort",
    "WorkspaceResources",
    "WorkspaceRoot",
    "WorkspaceRuntime",
    "WorkspaceRuntimeFactory",
)

WorkspaceRoot: typing.TypeAlias = str | os.PathLike[str]


@dataclass(frozen=True)
class WorkspaceResources:
    """保存待提交的工作区能力；创建方负责未提交资源的关闭。"""

    coding: "WorkspaceCodingPort"
    execution_policy: "ExecutionPolicy"
    image_reader: ImageReaderPort


class WorkspaceChangePort(typing.Protocol):
    """由 Harness 持有一次工作区切换，根会话结束后同步提交，调用方负责收尾。"""

    def commit(self) -> None:
        """提交已准备的依赖，不执行配置解析或外部 I/O。"""
        ...

    async def finish(self) -> tuple[str, ...]:
        """释放未采用或退役的资源，并返回可展示的清理错误。"""
        ...


class CodingRuntime(typing.Protocol):
    """定义工作区编码能力的资源生命周期契约。"""

    user_shell: UserShellPort

    async def close(self) -> None:
        """释放编码能力持有的全部资源。"""
        ...


class WorkspaceCodingPort(
    CodingRuntime,
    WorkspacePatchPort,
    WorkspaceProcessPort,
    typing.Protocol,
):
    """聚合一个工作区内共享生命周期的进程与补丁能力。"""


class PatchPreviewPort(typing.Protocol):
    """定义只读生成工作区补丁预览的端口。"""

    def __call__(
        self,
        *,
        patch: str,
        expected_sha256: dict[str, str] | None = None,
        force: bool = False,
    ) -> dict[str, typing.Any]:
        """返回补丁预览结果，不修改工作区。"""
        ...


@typing.runtime_checkable
class ExecutionPolicy(typing.Protocol):
    """定义绑定工作区的本地执行策略端口。"""

    def create_exec_approval_requirement_for_command(
        self,
        command: typing.Sequence[str] | str,
        *,
        approval_policy: str,
        sandbox_mode: str,
        cwd: WorkspaceRoot | None,
        tool: str,
        amendment_id: str,
        sandbox_permissions: object,
        environment_id: object,
        tty: object,
        additional_permissions: object,
        policy_fingerprint: object,
        patch_scope: object,
    ) -> ExecutionPolicyRequirement:
        """按当前策略判定命令是否允许、需要审批或禁止。"""
        ...

    def patch_scope_approved_for_session(
        self,
        patch_scope: object,
        *,
        cwd: WorkspaceRoot | None,
        environment_id: object,
    ) -> bool:
        """判断补丁范围是否已获当前会话批准。"""
        ...

    def add_patch_approval_for_session(
        self,
        patch_scope: object,
        *,
        cwd: WorkspaceRoot | None,
        environment_id: object,
    ) -> None:
        """记录当前会话已批准的补丁范围。"""
        ...

    def add_approval_for_session(
        self,
        command: typing.Sequence[str] | str,
        *,
        tool: str,
        cwd: WorkspaceRoot | None,
        sandbox_permissions: object,
        environment_id: object,
        tty: object,
        additional_permissions: object,
        policy_fingerprint: object,
        patch_scope: object,
    ) -> None:
        """记录当前会话已批准的命令形态。"""
        ...

    def persist_execpolicy_amendment(
        self,
        amendment: dict[str, object],
    ) -> os.PathLike[str]:
        """持久化用户确认的执行策略规则提案。"""
        ...


class CodingFactory(typing.Protocol):
    """定义组合根创建工作区编码能力的工厂契约。"""

    def __call__(
        self,
        *,
        root: WorkspaceRoot,
        application_layout: object | None,
        process_capability: ProcessCapability | None = None,
        interactive_process_capability: InteractiveProcessCapability | None = None,
        network_access: NetworkAccess = "restricted",
        network_policy: NetworkPolicyPort | None = None,
        network_blocked_handler_factory: NetworkBlockedHandlerFactory | None = None,
    ) -> WorkspaceCodingPort:
        ...


class ExecutionPolicyFactory(typing.Protocol):
    """定义组合根创建工作区执行策略的工厂契约。"""

    def __call__(self, *, workspace_root: WorkspaceRoot) -> ExecutionPolicy:
        ...


class WorkspaceRuntime(typing.Protocol):
    """定义 Controller 消费的工作区资源生命周期边界。"""

    coding: WorkspaceCodingPort
    execution_policy: ExecutionPolicy
    image_reader: ImageReaderPort
    user_shell: UserShellPort

    def prepare(
        self, workspace_root: WorkspaceRoot, *, network_access: NetworkAccess,
    ) -> WorkspaceResources:
        """准备新工作区资源，保留当前资源的所有权。"""
        ...

    def activate(self, resources: WorkspaceResources) -> WorkspaceResources:
        """同步采用准备资源并移交旧资源的清理责任。"""
        ...

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
        interactive_process_capability: InteractiveProcessCapability | None = None,
        network_access: NetworkAccess = "restricted",
        network_policy: NetworkPolicyPort | None = None,
        network_blocked_handler_factory: NetworkBlockedHandlerFactory | None = None,
    ) -> WorkspaceRuntime:
        ...


if __name__ == '__main__':
    pass
