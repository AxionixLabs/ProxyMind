# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from pathlib import Path
from agent.application.commands import TurnApplication
from agent.ports import (
    EffectJournal,
    TurnExecutorResult,
)
from agent.stores import (
    LocalEffectJournal,
    SQLiteRunStore,
)

ResultValue = typing.TypeVar("ResultValue", bound=TurnExecutorResult)


def open_turn_application(
    db_path: str | Path,
) -> TurnApplication[ResultValue]:
    """使用 Agent Runtime 专用 SQLite store 组合主动 Turn 应用入口。"""
    return TurnApplication(SQLiteRunStore(db_path))


def open_effect_journal(db_path: str | Path) -> EffectJournal:
    """使用独立 SQLite 文件组合本地效果账本端口。"""
    return LocalEffectJournal(db_path)


if __name__ == '__main__':
    pass
