# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass

FormattedText: typing.TypeAlias = list[tuple[str, str]]
FormattedLine: typing.TypeAlias = tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class LineFill(object):
    """描述单行内容随终端宽度延伸的填充规则。"""
    character: str
    margin: int = 0
    style: str | None = None


@dataclass(frozen=True, slots=True)
class FragmentBlock(object):
    """保存无需再次转换的 prompt_toolkit 文本片段。"""
    fragments: tuple[tuple[str, str], ...]
    line_fill: LineFill | None = None
    line_fills: tuple[LineFill | None, ...] = ()


if __name__ == '__main__':
    pass
