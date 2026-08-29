# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from agent.ports import (
    EffectJournal,
    EnvironmentSnapshotCapability,
    ModelCapability,
)
from .commands import TurnApplication

TurnApplicationFactory: typing.TypeAlias = Callable[
    [str | Path],
    TurnApplication[typing.Any],
]
EffectJournalFactory: typing.TypeAlias = Callable[[str | Path], EffectJournal]


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


if __name__ == '__main__':
    pass
