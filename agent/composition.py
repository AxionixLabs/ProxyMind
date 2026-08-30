# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from pathlib import Path
from agent.application.commands import TurnApplication
from agent.application.services import RuntimeServices
from agent.ports import (
    EffectJournal,
    TurnExecutorResult,
)
from agent.stores import (
    LocalEffectJournal,
    SQLiteRunStore,
)
from agent.capabilities import LocalEnvironmentSnapshotCapability
from agent.adapters import MindChatProtocolClient
from agent.ports import EnvironmentSnapshotCapability, ModelCapability

ResultValue = typing.TypeVar("ResultValue", bound=TurnExecutorResult)


def open_turn_application(
    db_path: str | Path,
) -> TurnApplication[ResultValue]:
    """使用 Agent Harness 专用 SQLite store 组合主动 Turn 应用入口。"""
    return TurnApplication(SQLiteRunStore(db_path))


def open_effect_journal(db_path: str | Path) -> EffectJournal:
    """使用独立 SQLite 文件组合本地效果账本端口。"""
    return LocalEffectJournal(db_path)


def open_model_capability() -> ModelCapability:
    """组合拥有 Session 水位的 mind.chat Protocol Client。"""
    return MindChatProtocolClient()


def open_environment_capability() -> EnvironmentSnapshotCapability:
    """组合进程级本机环境快照能力。"""
    return LocalEnvironmentSnapshotCapability()


def create_runtime_services() -> RuntimeServices:
    """创建供单个进程入口共享的 Agent Harness 依赖。"""
    return RuntimeServices(
        model_capability=open_model_capability(),
        environment_capability=open_environment_capability(),
        create_turn_application=open_turn_application,
        create_effect_journal=open_effect_journal,
    )


if __name__ == '__main__':
    pass
