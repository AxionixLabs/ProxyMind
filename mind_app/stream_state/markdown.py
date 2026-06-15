# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from pygments.token import Token
from rich.console import (
    Console,
    ConsoleOptions,
    RenderResult
)
from rich.markdown import (
    CodeBlock,
    Heading,
    ListItem,
    Markdown,
    TableDataElement
)
from rich.segment import Segment
from rich.style import Style
from rich.syntax import Syntax
from rich.syntax import ANSISyntaxTheme
from rich.text import Text
from rich.theme import Theme

MARKDOWN_CODE_STYLE = "#A8D5C2"

MARKDOWN_THEME = Theme({
    "markdown.code"       : MARKDOWN_CODE_STYLE,
    "markdown.code_block" : MARKDOWN_CODE_STYLE
})

MARKDOWN_CODE_THEME = ANSISyntaxTheme({
    Token                  : Style.parse("#BCC9D6"),
    Token.Comment          : Style.parse("dim #8FA4B8"),
    Token.Keyword          : Style.parse("bold #B9A6D8"),
    Token.Name             : Style.parse("#CAD5DF"),
    Token.Operator         : Style.parse("#AAB8C6"),
    Token.Literal.Number   : Style.parse("#D3C27C"),
    Token.Literal.String   : Style.parse("#A9CDBB"),
    Token.Generic.Deleted  : Style.parse("#FF8A8A"),
    Token.Generic.Inserted : Style.parse("#6EE7A8")
})


class LeftHeading(Heading):
    """按左对齐方式渲染 Markdown 标题。"""

    def __rich_console__(
        self,
        console: Console,
        options: ConsoleOptions
    ) -> RenderResult:
        """生成标题的 Rich 控制台片段。"""
        _ = console, options

        text = self.text or Text()
        text.justify = "left"

        yield text


class LeftTableDataElement(TableDataElement):
    """按左对齐方式创建 Markdown 表格单元格。"""

    @classmethod
    def create(cls, markdown: Markdown, token: typing.Any) -> "LeftTableDataElement":
        """根据 Markdown token 创建左对齐单元格元素。"""
        _ = markdown, token
        return cls(justify="left")


class NumberedListItem(ListItem):
    """渲染带句点的 Markdown 有序列表编号。"""

    def render_number(
        self,
        console: Console,
        options: ConsoleOptions,
        number: int,
        last_number: int
    ) -> RenderResult:
        """生成有序列表项的控制台片段。"""
        number_width   = len(str(last_number)) + 3
        render_options = options.update(width=options.max_width - number_width)
        lines          = console.render_lines(self.elements, render_options, style=self.style)
        number_style   = console.get_style("markdown.item.number", default="none")

        new_line = Segment("\n")
        padding  = Segment(" " * number_width, number_style)
        numeral  = Segment(f"{number}.".rjust(number_width - 1) + " ", number_style)

        for index, line in enumerate(lines):
            yield numeral if index == 0 else padding
            yield from line
            yield new_line


class PlainCodeBlock(CodeBlock):
    """渲染使用默认背景的 Markdown 代码块。"""

    def __rich_console__(
        self,
        console: Console,
        options: ConsoleOptions
    ) -> RenderResult:
        """生成代码块的 Rich 控制台片段。"""
        _ = console, options

        code = str(self.text).rstrip()

        yield Syntax(
            code,
            self.lexer_name,
            theme=MARKDOWN_CODE_THEME,
            word_wrap=True,
            background_color="default",
            padding=0
        )


class MarkdownRenderer(Markdown):
    """提供项目内统一 Markdown 元素映射和主题。"""

    elements = {
        **Markdown.elements,
        "heading_open"   : LeftHeading,
        "td_open"        : LeftTableDataElement,
        "th_open"        : LeftTableDataElement,
        "list_item_open" : NumberedListItem,
        "fence"          : PlainCodeBlock,
        "code_block"     : PlainCodeBlock
    }

    def __rich_console__(
        self,
        console: Console,
        options: ConsoleOptions
    ) -> RenderResult:
        """在临时主题范围内生成 Markdown 控制台片段。"""
        console.push_theme(MARKDOWN_THEME)

        try:
            yield from super().__rich_console__(console, options)
        finally:
            console.pop_theme()


def render_markdown(text: typing.Any) -> MarkdownRenderer:
    """创建项目内统一的 Markdown 渲染对象。"""
    return MarkdownRenderer(
        _preserve_blockquote_line_breaks(str(text or "")),
        justify="left",
        hyperlinks=False
    )


def _preserve_blockquote_line_breaks(text: str) -> str:
    """把连续引用行中的软换行转换为 Markdown 硬换行。"""
    lines = str(text or "").splitlines(keepends=True)
    if not lines:
        return ""

    out: list[str] = []
    for index, line in enumerate(lines):

        current   = line.rstrip("\r\n")
        newline   = line[len(current):]
        next_line = lines[index + 1].lstrip() if index + 1 < len(lines) else ""

        if current.lstrip().startswith(">") and next_line.startswith(">") and not current.endswith("  "):
            current = f"{current}  "

        out.append(f"{current}{newline}")

    return "".join(out)


if __name__ == '__main__':
    pass
