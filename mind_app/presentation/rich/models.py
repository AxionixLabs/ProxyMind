# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass

DisplayPart = dict[str, typing.Optional[str]]


@dataclass(frozen=True, slots=True)
class RenderedBlock(object):
    """保存当前终端输出需要的文本和样式片段。"""

    text: str
    display_parts: tuple[DisplayPart, ...]
    preserve_display_parts: bool = False


if __name__ == '__main__':
    pass
