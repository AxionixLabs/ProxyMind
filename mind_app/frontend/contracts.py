# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from abc import (
    ABC,
    abstractmethod
)
from dataclasses import (
    dataclass,
    field
)
from mind_app.interaction.contracts import InteractionPort
from mind_app.output.session import SessionFactory


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


@dataclass(frozen=True, slots=True)
class Frontend(object):
    """聚合应用级展示、交互和单轮输出装配能力。"""

    application: ApplicationSink
    interaction: InteractionPort
    session_factory: SessionFactory


if __name__ == '__main__':
    pass
