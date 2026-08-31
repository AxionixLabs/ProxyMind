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


__all__ = [
    "ApplicationSink",
    "ApplicationView",
    "Viewport",
]
