# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import replace
from markdown_it import MarkdownIt
from markdown_it.tree import SyntaxTreeNode
from pygments import lex
from pygments.lexers import get_lexer_by_name
from pygments.token import Token
from pygments.util import ClassNotFound
from mind_app.presentation.models import (
    StyledBlock,
    TextSpan,
    TextStyle
)
from mind_app.presentation.styles import (
    PREVIEW_CODE_COMMENT_STYLE,
    PREVIEW_CODE_KEYWORD_STYLE,
    PREVIEW_CODE_NAME_STYLE,
    PREVIEW_CODE_NUMBER_STYLE,
    PREVIEW_CODE_OPERATOR_STYLE,
    PREVIEW_CODE_STRING_STYLE,
    PREVIEW_CODE_TEXT_STYLE
)
from mind_app.stream_state.text_models import TextFinalUnit
from ..core.models import FragmentBlock
from ..core.styles import styled_block_fragments

MARKDOWN_HEADING_STYLE = TextStyle(foreground="#D7E7FF", bold=True)
MARKDOWN_MARKER_STYLE  = TextStyle(foreground="#8FA4B8", dim=True)
MARKDOWN_QUOTE_STYLE   = TextStyle(foreground="#A5B3C2", dim=True)
MARKDOWN_CODE_STYLE    = TextStyle(foreground="#A8D5C2")
MARKDOWN_LINK_STYLE    = TextStyle(foreground="#7DD3FC", underline=True)
MARKDOWN_RULE_STYLE    = TextStyle(foreground="#6F7A86", dim=True)

_MARKDOWN = MarkdownIt("commonmark")


def render_tui_final(units: tuple[TextFinalUnit, ...]) -> FragmentBlock:
    """把最终文本单元转换为 TUI 文本片段。"""
    spans: list[TextSpan] = []
    for unit in units:
        if spans:
            _append_span(spans, "\n\n" if unit.gap_before else "\n", TextStyle())
        elif unit.gap_before:
            _append_span(spans, "\n", TextStyle())

        if unit.kind == "markdown":
            _extend_spans(spans, _markdown_spans(unit.text))
        else:
            unit_spans = list(unit.spans) or [TextSpan(unit.text)]
            _extend_spans(spans, [
                span
                if span.style != TextStyle()
                else TextSpan(span.text, TextStyle(bold=True))
                for span in unit_spans
            ])

    while spans and spans[-1].text.endswith("\n"):
        text = spans[-1].text.rstrip("\n")
        if text:
            spans[-1] = TextSpan(text, spans[-1].style)
            break
        spans.pop()

    plain_text = "".join(span.text for span in spans)
    block      = StyledBlock(plain_text=plain_text, spans=tuple(spans))

    return FragmentBlock(styled_block_fragments(block))


def _markdown_spans(text: str) -> list[TextSpan]:
    """把 Markdown 文本解析为中立样式片段。"""
    root = SyntaxTreeNode(_MARKDOWN.parse(str(text or "")))

    lines = _render_blocks(root.children)
    while lines and not lines[-1]:
        lines.pop()

    spans: list[TextSpan] = []
    for index, line in enumerate(lines):
        if index:
            _append_span(spans, "\n", TextStyle())
        _extend_spans(spans, line)
    return spans


def _render_blocks(
    nodes: list[SyntaxTreeNode],
    *,
    list_depth: int = 0,
) -> list[list[TextSpan]]:
    """渲染一组块级 Markdown 节点。"""
    lines: list[list[TextSpan]] = []
    for node in nodes:
        rendered = _render_block(node, list_depth=list_depth)
        if not rendered:
            continue
        if lines and lines[-1] and rendered[0]:
            lines.append([])
        lines.extend(rendered)
    return lines


def _render_block(
    node: SyntaxTreeNode,
    *,
    list_depth: int,
) -> list[list[TextSpan]]:
    """渲染一个块级 Markdown 节点。"""
    if node.type == "paragraph":
        return _inline_lines(node.children)
    if node.type == "heading":
        return _inline_lines(node.children, base_style=MARKDOWN_HEADING_STYLE)
    if node.type in {"fence", "code_block"}:
        return _code_lines(node.content, language=node.info)
    if node.type == "bullet_list":
        return _list_lines(node, ordered=False, depth=list_depth)
    if node.type == "ordered_list":
        return _list_lines(node, ordered=True, depth=list_depth)
    if node.type == "blockquote":
        return _blockquote_lines(node, list_depth=list_depth)
    if node.type == "hr":
        return [[TextSpan("────────────", MARKDOWN_RULE_STYLE)]]
    if node.type in {"html_block", "text"}:
        return _plain_lines(node.content)
    if node.type == "inline":
        return _inline_lines([node])

    return _render_blocks(node.children, list_depth=list_depth)


def _list_lines(
    node: SyntaxTreeNode,
    *,
    ordered: bool,
    depth: int,
) -> list[list[TextSpan]]:
    """渲染有序或无序列表。"""
    lines: list[list[TextSpan]] = []

    start  = int(node.attrs.get("start") or 1) if ordered else 1
    indent = "  " * depth

    for index, item in enumerate(node.children):
        marker       = f"{start + index}. " if ordered else "- "
        marker_width = len(marker)

        item_lines: list[list[TextSpan]]   = []
        nested_lines: list[list[TextSpan]] = []

        for child in item.children:
            if child.type in {"bullet_list", "ordered_list"}:
                nested_lines.extend(_render_block(child, list_depth=depth + 1))
                continue
            rendered = _render_block(child, list_depth=depth + 1)
            if item_lines and item_lines[-1] and rendered and rendered[0]:
                item_lines.append([])
            item_lines.extend(rendered)

        if not item_lines:
            item_lines = [[]]

        first_content = True
        for line in item_lines:
            if line and first_content:
                lines.append([
                    TextSpan(indent, TextStyle()),
                    TextSpan(marker, MARKDOWN_MARKER_STYLE),
                    *line,
                ])
                first_content = False
            elif line:
                lines.append([
                    TextSpan(f"{indent}{' ' * marker_width}", TextStyle()),
                    *line,
                ])
            else:
                lines.append([])

        lines.extend(nested_lines)

    return lines


def _blockquote_lines(
    node: SyntaxTreeNode,
    *,
    list_depth: int,
) -> list[list[TextSpan]]:
    """渲染引用块并保留引用内换行。"""
    content = _render_blocks(node.children, list_depth=list_depth)

    return [
        [TextSpan("│ ", MARKDOWN_MARKER_STYLE), *(
            _apply_style(line, MARKDOWN_QUOTE_STYLE) if line else []
        )]
        for line in content
    ]


def _inline_lines(
    nodes: list[SyntaxTreeNode],
    *,
    base_style: TextStyle = TextStyle(),
) -> list[list[TextSpan]]:
    """渲染内联 Markdown 节点并按显式换行拆行。"""
    spans: list[TextSpan] = []
    for node in nodes:
        if node.type == "inline":
            _extend_spans(spans, _inline_spans(node.children, base_style))
        else:
            _extend_spans(spans, _inline_spans([node], base_style))
    return _split_lines(spans)


def _inline_spans(
    nodes: list[SyntaxTreeNode],
    style: TextStyle,
) -> list[TextSpan]:
    """递归渲染内联 Markdown 节点。"""
    spans: list[TextSpan] = []
    for node in nodes:
        if node.type == "text":
            _append_span(spans, node.content, style)
        elif node.type in {"softbreak", "hardbreak"}:
            _append_span(spans, "\n", style)
        elif node.type == "code_inline":
            _append_span(spans, node.content, _merge_style(style, MARKDOWN_CODE_STYLE))
        elif node.type == "strong":
            _extend_spans(spans, _inline_spans(
                node.children,
                replace(style, bold=True),
            ))
        elif node.type == "em":
            _extend_spans(spans, _inline_spans(
                node.children,
                replace(style, italic=True),
            ))
        elif node.type == "link":
            _extend_spans(spans, _inline_spans(
                node.children,
                _merge_style(style, MARKDOWN_LINK_STYLE),
            ))
        elif node.type == "image":
            alt = node.attrs.get("alt") or node.content
            _append_span(spans, str(alt or ""), _merge_style(style, MARKDOWN_LINK_STYLE))
        elif node.children:
            _extend_spans(spans, _inline_spans(node.children, style))
        elif node.content:
            _append_span(spans, node.content, style)

    return spans


def _code_lines(text: str, *, language: str) -> list[list[TextSpan]]:
    """使用 Pygments 渲染代码块。"""
    code = str(text or "").rstrip("\n")
    if not code:
        return [[]]

    lexer = None

    lexer_name = str(language or "").strip().split(maxsplit=1)[0]
    if lexer_name:
        try:
            lexer = get_lexer_by_name(lexer_name)
        except ClassNotFound:
            lexer = None

    if lexer is None:
        return [[TextSpan(line, MARKDOWN_CODE_STYLE)] for line in code.split("\n")]

    spans: list[TextSpan] = []

    try:
        for token_type, value in lex(code, lexer):
            _append_span(spans, value, _code_style(token_type))
    except (TypeError, ValueError):
        return [[TextSpan(line, MARKDOWN_CODE_STYLE)] for line in code.split("\n")]

    return _split_lines(spans)


def _code_style(token_type: typing.Any) -> TextStyle:
    """把 Pygments token 映射为代码块样式。"""
    if token_type in Token.Keyword:
        return PREVIEW_CODE_KEYWORD_STYLE
    if token_type in Token.Name:
        return PREVIEW_CODE_NAME_STYLE
    if token_type in Token.String:
        return PREVIEW_CODE_STRING_STYLE
    if token_type in Token.Number:
        return PREVIEW_CODE_NUMBER_STYLE
    if token_type in Token.Comment:
        return PREVIEW_CODE_COMMENT_STYLE
    if token_type in Token.Operator or token_type in Token.Punctuation:
        return PREVIEW_CODE_OPERATOR_STYLE

    return PREVIEW_CODE_TEXT_STYLE


def _plain_lines(text: str) -> list[list[TextSpan]]:
    """把纯文本拆成无样式行。"""
    return [[TextSpan(line)] for line in str(text or "").rstrip("\n").split("\n")]


def _split_lines(spans: list[TextSpan]) -> list[list[TextSpan]]:
    """按片段中的换行拆分文本行。"""
    lines: list[list[TextSpan]] = [[]]
    for span in spans:
        chunks = span.text.split("\n")
        for index, chunk in enumerate(chunks):
            if chunk:
                _append_span(lines[-1], chunk, span.style)
            if index < len(chunks) - 1:
                lines.append([])

    if lines and not lines[-1]:
        lines.pop()

    return lines or [[]]


def _apply_style(spans: list[TextSpan], style: TextStyle) -> list[TextSpan]:
    """把基础样式合并到一行片段。"""
    return [TextSpan(span.text, _merge_style(span.style, style)) for span in spans]


def _merge_style(base: TextStyle, overlay: TextStyle) -> TextStyle:
    """合并两项中立文本样式。"""
    return TextStyle(
        foreground=overlay.foreground or base.foreground,
        background=overlay.background or base.background,
        bold=base.bold or overlay.bold,
        dim=base.dim or overlay.dim,
        italic=base.italic or overlay.italic,
        underline=base.underline or overlay.underline,
        reverse=base.reverse or overlay.reverse,
    )


def _extend_spans(target: list[TextSpan], source: list[TextSpan]) -> None:
    """追加并合并一组相邻样式片段。"""
    for span in source:
        _append_span(target, span.text, span.style)


def _append_span(spans: list[TextSpan], text: str, style: TextStyle) -> None:
    """追加片段并合并相邻同样式内容。"""
    if not text:
        return None
    if spans and spans[-1].style == style:
        previous = spans[-1]
        spans[-1] = TextSpan(f"{previous.text}{text}", style)
        return None
    spans.append(TextSpan(text, style))


if __name__ == '__main__':
    pass
