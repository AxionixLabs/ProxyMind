# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from rich.console import (
    Console,
    ConsoleOptions,
    RenderResult
)
from rich.markdown import (
    Heading,
    Markdown,
    TableDataElement
)
from rich.text import Text


class LeftHeading(Heading):
    """左对齐渲染 Markdown 标题。"""

    def __rich_console__(
        self,
        console: Console,
        options: ConsoleOptions
    ) -> RenderResult:
        _ = console, options

        text = self.text or Text()
        text.justify = "left"

        yield text


class LeftTableDataElement(TableDataElement):
    """左对齐渲染 Markdown 表格单元格。"""

    @classmethod
    def create(cls, markdown: Markdown, token: typing.Any) -> "LeftTableDataElement":
        _ = markdown, token
        return cls(justify="left")


class MarkdownRenderer(Markdown):
    """统一的 Markdown 渲染器，保留 Rich 能力并修正默认布局。"""

    elements = {
        **Markdown.elements,
        "heading_open" : LeftHeading,
        "td_open"      : LeftTableDataElement,
        "th_open"      : LeftTableDataElement
    }


def render_markdown(text: typing.Any) -> MarkdownRenderer:
    """创建统一的 Markdown 渲染对象。"""
    return MarkdownRenderer(
        str(text or ""), justify="left", hyperlinks=False
    )


if __name__ == '__main__':
    pass
