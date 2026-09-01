# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ToolStartView:
    """描述普通工具开始执行时的展示数据。"""

    name: str
    arguments: dict[str, typing.Any]
    call_id: str = ""


@dataclass(frozen=True, slots=True)
class GenericToolResultView:
    """描述普通工具执行结果的展示数据。"""

    name: str
    text: str
    ok: bool
    call_id: str = ""


@dataclass(frozen=True, slots=True)
class NativeToolResultView:
    """描述原生编码工具执行结果的展示数据。"""

    name: str
    arguments: dict[str, typing.Any]
    ok: bool
    data: typing.Any
    cost_ms: int | None
    call_id: str = ""


@dataclass(frozen=True, slots=True)
class BatchCallView:
    """描述并行批次中的单个工具调用。"""

    name: str
    arguments: dict[str, typing.Any]


@dataclass(frozen=True, slots=True)
class BatchStartView:
    """描述并行工具开始执行时的展示数据。"""

    calls: tuple[BatchCallView, ...]


@dataclass(frozen=True, slots=True)
class BatchResultView:
    """描述并行批次中的单个工具结果。"""

    name: str
    ok: bool
    text: str


@dataclass(frozen=True, slots=True)
class BatchCompletedView:
    """描述并行工具执行完成时的展示数据。"""

    results: tuple[BatchResultView, ...]


if __name__ == '__main__':
    pass
