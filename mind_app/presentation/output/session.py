# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass
from agent.application.views.contracts import PresentationSink
from .content import ContentSink
from agent.ports import (
    OutputControlPort,
    OutputStatusPort
)


@dataclass(frozen=True, slots=True)
class OutputSession(object):
    """聚合单轮运行所需的控制、正文和结构化展示边界。"""
    control: OutputControlPort
    status: OutputStatusPort
    content: ContentSink
    presentation: PresentationSink
    show_hook_lifecycle: bool = False


SessionFactory: typing.TypeAlias = typing.Callable[..., OutputSession]


if __name__ == '__main__':
    pass
