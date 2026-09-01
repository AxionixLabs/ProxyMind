# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from agent.ports import (
    ApprovalCoordinatorPort,
    EffectJournalFactory,
    EnvironmentSnapshotCapability,
    HelixCapability,
    HookRegistryFactory,
    McpRuntimeBuilder,
    ModelCapability,
    ProcessCapability,
    PermissionGrantPort,
    SkillsProvider,
    SubagentControlPort,
    SubscriptionRuntimeBuilder,
    ToolRegistryPort,
    ToolRuntimeBuilder,
)
from agent.ports.media import ImageReaderPort
from agent.ports.workspace import (
    ExecutionPolicy,
    WorkspaceCodingPort,
    WorkspaceRuntimeFactory,
)
from .config.settings import FeatureSettings
from .tools.execution import ToolExecutionAdapter
from .turns.commands import TurnApplication

TurnApplicationFactory: typing.TypeAlias = Callable[
    [str | Path],
    TurnApplication[typing.Any],
]

SkillsConfigReader: typing.TypeAlias = Callable[[], dict[str, typing.Any]]

SkillsProviderFactory: typing.TypeAlias = Callable[[SkillsConfigReader], SkillsProvider]


class ClientToolRegistryBuilder(typing.Protocol):
    """定义组合根创建工作区客户端工具注册表的工厂。"""

    def __call__(
        self,
        coding: WorkspaceCodingPort,
        *,
        image_reader: ImageReaderPort,
        execution_policy: ExecutionPolicy | None,
        subagent_runtime: SubagentControlPort | None,
        approval_coordinator: ApprovalCoordinatorPort | None,
        features: FeatureSettings | None,
    ) -> ToolRegistryPort:
        """使用已组合能力创建一个新的客户端工具注册表。"""
        ...


class BuiltinToolRegistryBuilder(typing.Protocol):
    """定义组合根创建 Harness 内置工具注册表的工厂。"""

    def __call__(
        self,
        *,
        approval_coordinator: ApprovalCoordinatorPort | None,
        permission_grants: PermissionGrantPort,
        features: FeatureSettings,
    ) -> ToolRegistryPort:
        """使用已组合能力创建一个新的内置工具注册表。"""
        ...


@dataclass(frozen=True, slots=True)
class RuntimeServices:
    """声明进程级运行能力及其短生命周期应用对象工厂。

    进程组合根只创建一个实例并注入全部入站适配器；实现方必须返回绑定到给定
    路径的新 application 或 journal，不得在 application 层读取环境或选择实现。
    """

    model_capability: ModelCapability
    environment_capability: EnvironmentSnapshotCapability
    create_turn_application: TurnApplicationFactory
    create_effect_journal: EffectJournalFactory
    tool_execution: ToolExecutionAdapter
    create_hook_registry: HookRegistryFactory
    create_client_tool_registry: ClientToolRegistryBuilder
    create_builtin_tool_registry: BuiltinToolRegistryBuilder
    create_tool_runtime: ToolRuntimeBuilder
    create_mcp_runtime: McpRuntimeBuilder | None = None
    create_subscription_runtime: SubscriptionRuntimeBuilder | None = None
    create_workspace_runtime: WorkspaceRuntimeFactory | None = None
    process_capability: ProcessCapability | None = None
    helix_capability: HelixCapability | None = None
    create_skills_provider: SkillsProviderFactory | None = None

    def __post_init__(self) -> None:
        """拒绝缺失能力，确保组合错误在启动边界暴露。"""
        if not isinstance(self.model_capability, ModelCapability):
            raise TypeError("model capability does not implement ModelCapability")
        if not isinstance(
            self.environment_capability,
            EnvironmentSnapshotCapability,
        ):
            raise TypeError(
                "environment capability does not implement "
                "EnvironmentSnapshotCapability"
            )
        if not callable(self.create_turn_application):
            raise TypeError("turn application factory must be callable")
        if not callable(self.create_effect_journal):
            raise TypeError("effect journal factory must be callable")
        if not isinstance(self.tool_execution, ToolExecutionAdapter):
            raise TypeError("tool execution adapter is invalid")
        if not callable(self.create_hook_registry):
            raise TypeError("hook registry factory must be callable")
        if not callable(self.create_client_tool_registry):
            raise TypeError("client tool registry factory must be callable")
        if not callable(self.create_builtin_tool_registry):
            raise TypeError("builtin tool registry factory must be callable")
        if not callable(self.create_tool_runtime):
            raise TypeError("tool runtime factory must be callable")
        if (
            self.create_mcp_runtime is not None
            and not callable(self.create_mcp_runtime)
        ):
            raise TypeError("MCP runtime factory must be callable")
        if (
            self.create_subscription_runtime is not None
            and not callable(self.create_subscription_runtime)
        ):
            raise TypeError("subscription runtime factory must be callable")
        if (
            self.create_skills_provider is not None
            and not callable(self.create_skills_provider)
        ):
            raise TypeError("skills provider factory must be callable")
        if (
            self.create_workspace_runtime is not None
            and not callable(self.create_workspace_runtime)
        ):
            raise TypeError("workspace runtime factory must be callable")
        if (
            self.process_capability is not None
            and not isinstance(self.process_capability, ProcessCapability)
        ):
            raise TypeError("process capability is invalid")
        if (
            self.helix_capability is not None
            and not isinstance(self.helix_capability, HelixCapability)
        ):
            raise TypeError("Helix capability is invalid")


if __name__ == '__main__':
    pass
