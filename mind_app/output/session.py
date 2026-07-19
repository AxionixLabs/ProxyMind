# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from dataclasses import dataclass
from mind_app.presentation.contracts import PresentationSink
from .content import ContentSink
from .contracts import OutputPort


@dataclass(frozen=True, slots=True)
class OutputSession(object):
    """聚合单轮运行所需的控制、正文和结构化展示边界。"""

    control: OutputPort
    content: ContentSink
    presentation: PresentationSink


if __name__ == '__main__':
    pass
