# -*- coding: utf-8 -*-

import typing
from abc import (
    ABC,
    abstractmethod,
)
from dataclasses import (
    dataclass,
    field,
)


@dataclass(frozen=True, slots=True)
class ApplicationView(object):
    """描述应用生命周期中的一项展示数据。"""

    type: str
    renderable: typing.Any = None
    end: str = "\n"
    payload: dict[str, typing.Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Viewport(object):
    """描述前端当前可用的展示尺寸。"""

    width: int | None = None
    height: int | None = None


@dataclass(frozen=True, slots=True)
class TextStyle(object):
    """描述与终端实现无关的文本样式。"""

    foreground: str | None = None
    background: str | None = None
    bold: bool = False
    dim: bool = False
    italic: bool = False
    underline: bool = False
    reverse: bool = False
    strikethrough: bool = False


@dataclass(frozen=True, slots=True)
class TextSpan(object):
    """保存一段文本及其中立样式。"""

    text: str = field()
    style: TextStyle = TextStyle()
    hyperlink: str | None = None


@dataclass(frozen=True, slots=True)
class StyledBlock(object):
    """保存一个结构化文本块及其纯文本表示。"""

    plain_text: str
    spans: tuple[TextSpan, ...] = ()
    preserve_spans: bool = False
    direct: bool = False
    line_fill_styles: tuple[TextStyle | None, ...] = ()


class ApplicationSink(ABC):
    """接收跨单轮存在的应用级展示数据。"""

    @property
    @abstractmethod
    def viewport(self) -> Viewport:
        """返回当前前端展示尺寸。"""
        raise NotImplementedError

    @abstractmethod
    def emit(self, view: ApplicationView) -> None:
        """发送一项应用级展示数据。"""
        raise NotImplementedError


class TurnForegroundLifecyclePort(typing.Protocol):
    """定义终端前台轮次进度、动画和资源清理端口。"""

    @property
    def application(self) -> ApplicationSink:
        """返回用于投影 worked footer 的应用展示端。"""
        ...

    @property
    def animate(self) -> bool:
        """返回当前前台是否启用轮次动画。"""
        ...

    def begin_terminal_progress(self) -> None:
        """启动终端轮次进度。"""
        ...

    def end_terminal_progress(self) -> None:
        """结束终端轮次进度。"""
        ...

    async def start_animation(self) -> None:
        """开始当前轮次动画。"""
        ...

    def finish_turn_wait(self) -> None:
        """结束当前轮次等待展示。"""
        ...

    def emit_worked_footer(self, elapsed_seconds: float) -> None:
        """提交当前轮次完成后的耗时展示。"""
        ...

    async def stop_animation(self) -> None:
        """停止当前轮次动画。"""
        ...

    async def await_cleanup(self, awaitable: typing.Awaitable[None]) -> None:
        """等待动画和其他异步资源完成清理。"""
        ...


__all__ = [
    "ApplicationSink",
    "TurnForegroundLifecyclePort",
    "ApplicationView",
    "StyledBlock",
    "TextSpan",
    "TextStyle",
    "Viewport",
]
