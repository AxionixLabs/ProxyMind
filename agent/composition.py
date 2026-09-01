# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import functools
import typing
from pathlib import Path
from observability import observe_exception
from agent.application.turns.commands import TurnApplication
from agent.application.services import (
    BuiltinToolRegistryBuilder,
    ClientToolRegistryBuilder,
    RuntimeServices,
)
from agent.application.services import SkillsConfigReader
from agent.application.tools.execution import ToolExecutionAdapter
from agent.ports import (
    EffectJournal,
    SkillsProvider,
    TurnExecutorResult,
)
from agent.ports import (
    HookRegistryFactory,
    McpRuntimeBuilder,
    SubscriptionRuntimeBuilder,
    ToolRuntimeBuilder,
)
from agent.stores import (
    LocalEffectJournal,
    SQLiteRunStore,
)
from agent.capabilities import (
    LocalEnvironmentSnapshotCapability,
    LocalProcessCapability,
)
from agent.adapters import MindChatProtocolClient
from agent.ports import EnvironmentSnapshotCapability, ModelCapability
from agent.ports.workspace import WorkspaceRuntimeFactory
from agent.harness.sessions.owner import SessionRuntimeOwner

ResultValue = typing.TypeVar("ResultValue", bound=TurnExecutorResult)
SkillsPayloadBuilder: typing.TypeAlias = typing.Callable[
    [dict[str, typing.Any]],
    list[dict[str, str]],
]


def open_turn_application(
    db_path: str | Path,
) -> TurnApplication[ResultValue]:
    """使用 Agent Harness 专用 SQLite store 组合主动 Turn 应用入口。"""
    return TurnApplication(
        SQLiteRunStore(db_path),
        runtime_factory=SessionRuntimeOwner,
    )


def open_effect_journal(db_path: str | Path) -> EffectJournal:
    """使用独立 SQLite 文件组合本地效果账本端口。"""
    return LocalEffectJournal(db_path)


def open_model_capability() -> ModelCapability:
    """组合拥有 Session 水位的 mind.chat Protocol Client。"""
    return MindChatProtocolClient()


def open_environment_capability() -> EnvironmentSnapshotCapability:
    """组合进程级本机环境快照能力。"""
    return LocalEnvironmentSnapshotCapability()


def open_process_capability() -> LocalProcessCapability:
    """组合标准库实现的本地进程能力。"""
    return LocalProcessCapability()


def open_skills_provider(
    config_reader: SkillsConfigReader,
    *,
    payload_builder: SkillsPayloadBuilder,
) -> SkillsProvider:
    """在组合根绑定配置读取器并返回稳定的 skills 快照提供器。"""
    if not callable(config_reader):
        raise TypeError("skills config reader must be callable")
    if not callable(payload_builder):
        raise TypeError("skills payload builder must be callable")

    def provide() -> list[dict[str, str]]:
        """读取当前配置并转换为模型可见的 skills 描述。"""
        try:
            return payload_builder(config_reader())
        except (OSError, TypeError, ValueError) as error:
            observe_exception(
                "subagent.skills.resolve_failed",
                error,
                level="WARNING",
            )
            return []

    return provide


def create_runtime_services(
    *,
    effect_journal_path: str | Path,
    create_hook_registry: HookRegistryFactory,
    create_client_tool_registry: ClientToolRegistryBuilder,
    create_builtin_tool_registry: BuiltinToolRegistryBuilder,
    create_tool_runtime: ToolRuntimeBuilder,
    tool_execution: ToolExecutionAdapter,
    create_mcp_runtime: McpRuntimeBuilder | None = None,
    create_subscription_runtime: SubscriptionRuntimeBuilder | None = None,
    skills_payload_builder: SkillsPayloadBuilder,
    create_workspace_runtime: WorkspaceRuntimeFactory | None = None,
) -> RuntimeServices:
    """创建供单个进程入口共享的 Agent Harness 依赖。"""
    if not callable(skills_payload_builder):
        raise TypeError("skills payload builder must be callable")
    return RuntimeServices(
        model_capability=open_model_capability(),
        environment_capability=open_environment_capability(),
        create_turn_application=open_turn_application,
        create_effect_journal=functools.partial(
            open_effect_journal,
            effect_journal_path,
        ),
        tool_execution=tool_execution,
        create_hook_registry=create_hook_registry,
        create_client_tool_registry=create_client_tool_registry,
        create_builtin_tool_registry=create_builtin_tool_registry,
        create_tool_runtime=create_tool_runtime,
        create_mcp_runtime=create_mcp_runtime,
        create_subscription_runtime=create_subscription_runtime,
        create_workspace_runtime=create_workspace_runtime,
        process_capability=open_process_capability(),
        create_skills_provider=(
            lambda reader: open_skills_provider(
                reader,
                payload_builder=skills_payload_builder,
            )
        ),
    )


if __name__ == '__main__':
    pass
