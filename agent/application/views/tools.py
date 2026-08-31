# -*- coding: utf-8 -*-

import typing
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TracePreview:
    """保存完整预览、屏幕预览和省略行数。"""

    full: str = ""
    screen: str = ""
    omitted_lines: int = 0
    kind: str = "text"


@dataclass(frozen=True, slots=True)
class TraceEntry:
    """保存一条可独立展示的工具轨迹。"""

    title: str
    preview: TracePreview
    ok: bool = True


@dataclass(frozen=True, slots=True)
class ToolStartView:
    """描述普通工具开始执行时的展示数据。"""

    name: str
    arguments: dict[str, typing.Any]
    title: str
    preview: TracePreview
    call_id: str = ""


@dataclass(frozen=True, slots=True)
class GenericToolResultView:
    """描述普通工具执行结果的展示数据。"""

    name: str
    text: str
    ok: bool
    title: str
    preview: TracePreview
    call_id: str = ""


@dataclass(frozen=True, slots=True)
class NativeToolResultView:
    """描述原生编码工具执行结果的展示数据。"""

    name: str
    arguments: dict[str, typing.Any]
    ok: bool
    data: typing.Any
    cost_ms: int | None
    entries: tuple[TraceEntry, ...]
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
