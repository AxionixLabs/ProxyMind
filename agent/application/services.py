# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from agent.ports import (
    EffectJournal,
    EnvironmentSnapshotCapability,
    HelixCapability,
    HookRegistryFactory,
    McpRuntimeBuilder,
    ModelCapability,
    ProcessCapability,
    SkillsProvider,
)
from agent.ports.workspace import WorkspaceRuntimeFactory
from .turns.commands import TurnApplication

TurnApplicationFactory: typing.TypeAlias = Callable[
    [str | Path],
    TurnApplication[typing.Any],
]
EffectJournalFactory: typing.TypeAlias = Callable[[str | Path], EffectJournal]
SkillsConfigReader: typing.TypeAlias = Callable[[], dict[str, typing.Any]]
SkillsProviderFactory: typing.TypeAlias = Callable[[SkillsConfigReader], SkillsProvider]


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
    create_hook_registry: HookRegistryFactory
    create_mcp_runtime: McpRuntimeBuilder | None = None
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
        if not callable(self.create_hook_registry):
            raise TypeError("hook registry factory must be callable")
        if (
            self.create_mcp_runtime is not None
            and not callable(self.create_mcp_runtime)
        ):
            raise TypeError("MCP runtime factory must be callable")
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
