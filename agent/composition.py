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
from agent.capabilities import RemoteModelCapability
from agent.ports import ModelCapability

ResultValue = typing.TypeVar("ResultValue", bound=TurnExecutorResult)


def open_turn_application(
    db_path: str | Path,
) -> TurnApplication[ResultValue]:
    """使用 Agent Runtime 专用 SQLite store 组合主动 Turn 应用入口。"""
    return TurnApplication(SQLiteRunStore(db_path))


def open_effect_journal(db_path: str | Path) -> EffectJournal:
    """使用独立 SQLite 文件组合本地效果账本端口。"""
    return LocalEffectJournal(db_path)


def open_model_capability() -> ModelCapability:
    """组合正式协议模型事件流的远端能力实现。"""
    return RemoteModelCapability()


def create_runtime_services() -> RuntimeServices:
    """创建供单个进程入口共享的 Agent Runtime 依赖。"""
    return RuntimeServices(
        model_capability=open_model_capability(),
        create_turn_application=open_turn_application,
        create_effect_journal=open_effect_journal,
    )


if __name__ == '__main__':
    pass
