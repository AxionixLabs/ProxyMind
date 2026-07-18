# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass
from markdown_it import MarkdownIt
from markdown_it.token import Token
from prompt_toolkit.formatted_text import StyleAndTextTuples

from .cells import TranscriptCell


@dataclass(frozen=True, slots=True)
class RenderedTranscript:
    """保存会话的可见文本和逐行样式片段。"""

    text: str
    lines: tuple[tuple[tuple[str, str], ...], ...]


@dataclass(slots=True)
class _ListState:
    ordered: bool
    next_index: int = 1
    current_prefix: str = ""


class TranscriptRenderer:
    """把类型化会话单元投影为可选择的富文本。"""

    def __init__(self) -> None:
        """初始化 CommonMark 解析器。"""
        self._markdown = MarkdownIt("commonmark")

    def render(self, cells: tuple[TranscriptCell, ...]) -> RenderedTranscript:
        """渲染会话单元并在单元间保留一个空行。"""
        lines: list[StyleAndTextTuples] = []
        for cell in cells:
            cell_lines = self._render_cell(cell)
            if not cell_lines:
                continue
            if lines:
                lines.append([])
            lines.extend(cell_lines)

        text = "\n".join(
            "".join(fragment for _, fragment in line)
            for line in lines
        )
        frozen_lines = tuple(
            tuple((style, fragment) for style, fragment in line)
            for line in lines
        )
        return RenderedTranscript(text=text, lines=frozen_lines)

    def _render_cell(self, cell: TranscriptCell) -> list[StyleAndTextTuples]:
        """根据单元类型选择 Markdown 或普通文本渲染。"""
        text = cell.text.strip("\n")
        if not text:
            return []
        if cell.kind == "header":
            return [
                _render_header_line(line)
                for line in text.split("\n")
            ]
        if cell.kind == "assistant" and not cell.streaming:
            return self._render_markdown(text)
        return [
            [(_plain_line_style(cell.kind, line), line)]
            for line in text.split("\n")
        ]

    def _render_markdown(self, text: str) -> list[StyleAndTextTuples]:
        """把 CommonMark 块级和行内节点转换为逐行样式片段。"""
        tokens = self._markdown.parse(text)
        lines: list[StyleAndTextTuples] = []
        list_stack: list[_ListState] = []
        quote_depth = 0
        heading_level = 0
        last_block_was_list = False

        for token in tokens:
            if token.type == "bullet_list_open":
                list_stack.append(_ListState(ordered=False))
                continue
            if token.type == "ordered_list_open":
                start = _token_attribute(token, "start")
                list_stack.append(
                    _ListState(ordered=True, next_index=int(start or 1))
                )
                continue
            if token.type in {"bullet_list_close", "ordered_list_close"}:
                if list_stack:
                    list_stack.pop()
                continue
            if token.type == "list_item_open":
                if list_stack:
                    current = list_stack[-1]
                    marker = (
                        f"{current.next_index}. "
                        if current.ordered
                        else "• "
                    )
                    current.current_prefix = "  " * (len(list_stack) - 1) + marker
                    current.next_index += 1
                continue
            if token.type == "list_item_close":
                if list_stack:
                    list_stack[-1].current_prefix = ""
                continue
            if token.type == "blockquote_open":
                quote_depth += 1
                continue
            if token.type == "blockquote_close":
                quote_depth = max(0, quote_depth - 1)
                continue
            if token.type == "heading_open":
                heading_level = _heading_level(token.tag)
                continue
            if token.type == "heading_close":
                heading_level = 0
                continue
            if token.type == "inline":
                prefix = _block_prefix(list_stack, quote_depth)
                current_block_is_list = bool(list_stack)
                inline_lines = _render_inline(
                    token.children or [],
                    heading_level=heading_level
                )
                _append_block(
                    lines,
                    _prefix_lines(inline_lines, prefix),
                    compact=current_block_is_list and last_block_was_list
                )
                last_block_was_list = current_block_is_list
                continue
            if token.type in {"fence", "code_block"}:
                code_lines: list[StyleAndTextTuples] = []
                language = str(token.info or "").strip().split(maxsplit=1)[0]
                if language:
                    code_lines.append([
                        ("class:markdown.code.language", language)
                    ])
                for line in token.content.rstrip("\n").split("\n"):
                    code_lines.append([("class:markdown.code.block", line)])
                prefix = _block_prefix(list_stack, quote_depth, code=True)
                _append_block(
                    lines,
                    _prefix_lines(code_lines, prefix),
                    compact=bool(list_stack) and last_block_was_list
                )
                last_block_was_list = bool(list_stack)
                continue
            if token.type == "hr":
                _append_block(
                    lines,
                    [[("class:markdown.rule", "─" * 32)]],
                    compact=False
                )
                last_block_was_list = False

        return lines or [[("class:assistant", text)]]


def _render_inline(
    children: list[Token],
    *,
    heading_level: int
) -> list[StyleAndTextTuples]:
    """渲染 Markdown 行内节点并保留显式换行。"""
    lines: list[StyleAndTextTuples] = [[]]
    styles: list[str] = []
    heading_style = (
        f"class:markdown.heading.{min(heading_level, 3)}"
        if heading_level
        else "class:assistant"
    )

    for child in children:
        if child.type in {"softbreak", "hardbreak"}:
            lines.append([])
            continue
        if child.type in {"strong_open", "em_open", "link_open"}:
            styles.append(_inline_style(child.type))
            continue
        if child.type in {"strong_close", "em_close", "link_close"}:
            if styles:
                styles.pop()
            continue
        if child.type == "code_inline":
            lines[-1].append(("class:markdown.code.inline", child.content))
            continue
        if child.type == "image":
            text = child.content or str(_token_attribute(child, "alt") or "image")
            lines[-1].append(("class:markdown.image", text))
            continue
        content = str(child.content or "")
        if not content:
            continue
        style = " ".join([heading_style, *styles]).strip()
        lines[-1].append((style, content))
    return lines


def _inline_style(token_type: str) -> str:
    """返回行内节点对应的样式类。"""
    return {
        "strong_open": "class:markdown.strong",
        "em_open": "class:markdown.emphasis",
        "link_open": "class:markdown.link"
    }[token_type]


def _append_block(
    output: list[StyleAndTextTuples],
    block: list[StyleAndTextTuples],
    *,
    compact: bool
) -> None:
    """把内容块追加到输出并归一化块间距。"""
    if not block:
        return
    if output and not compact and output[-1]:
        output.append([])
    output.extend(block)


def _prefix_lines(
    lines: list[StyleAndTextTuples],
    prefix: StyleAndTextTuples
) -> list[StyleAndTextTuples]:
    """为列表、引用和代码内容增加层级前缀。"""
    if not prefix:
        return lines
    width = sum(len(text) for _, text in prefix)
    continuation = [("", " " * width)]
    return [
        [*(prefix if index == 0 else continuation), *line]
        for index, line in enumerate(lines)
    ]


def _block_prefix(
    lists: list[_ListState],
    quote_depth: int,
    *,
    code: bool = False
) -> StyleAndTextTuples:
    """生成当前列表和引用层级的可见前缀。"""
    parts: StyleAndTextTuples = []
    if quote_depth:
        parts.append(("class:markdown.quote", "│ " * quote_depth))
    if lists:
        marker = lists[-1].current_prefix
        if marker:
            parts.append(("class:markdown.list.marker", marker))
    elif code:
        parts.append(("", "  "))
    return parts


def _render_header_line(line: str) -> StyleAndTextTuples:
    """将标题中的品牌名与弱化文本拆分渲染。"""
    before, brand, after = line.partition("Mind")
    if not brand:
        return [("class:header.dim", line)]
    fragments: StyleAndTextTuples = []
    if before:
        fragments.append(("class:header.dim", before))
    fragments.append(("class:header.brand", brand))
    if after:
        fragments.append(("class:header.dim", after))
    return fragments


def _plain_line_style(kind: str, line: str) -> str:
    """返回非 Markdown 内容行的界面样式。"""
    stripped = line.lstrip()
    if kind == "header":
        return "class:header"
    if kind == "user":
        return "class:user"
    if kind == "error":
        return "class:trace.title"
    if stripped.startswith(("└", "├", "│")):
        return "class:trace"
    if stripped.startswith("• "):
        return "class:trace.title"
    return "class:assistant" if kind == "assistant" else "class:trace"


def _heading_level(tag: str) -> int:
    """从标题标签中读取有界的层级。"""
    try:
        return min(6, max(1, int(str(tag).lstrip("h"))))
    except ValueError:
        return 1


def _token_attribute(token: Token, name: str) -> typing.Any:
    """返回 Markdown 节点的指定属性。"""
    attrs = token.attrs
    return attrs.get(name) if isinstance(attrs, dict) else None


if __name__ == '__main__':
    pass
