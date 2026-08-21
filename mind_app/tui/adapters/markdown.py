# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import typing
from dataclasses import (
    dataclass,
    replace
)
from markdown_it import MarkdownIt
from markdown_it.tree import SyntaxTreeNode
from prompt_toolkit.utils import get_cwidth
from pygments import lex
from pygments.lexers import get_lexer_by_name
from pygments.token import Token
from pygments.util import ClassNotFound
from mind_app.presentation.models import (
    StyledBlock,
    TextSpan,
    TextStyle
)
from mind_app.presentation.terminal_text import sanitize_styled_block
from mind_app.presentation.styles import (
    PREVIEW_CODE_COMMENT_STYLE,
    PREVIEW_CODE_KEYWORD_STYLE,
    PREVIEW_CODE_NAME_STYLE,
    PREVIEW_CODE_NUMBER_STYLE,
    PREVIEW_CODE_OPERATOR_STYLE,
    PREVIEW_CODE_STRING_STYLE,
    PREVIEW_CODE_TEXT_STYLE
)
from ..core.models import FragmentBlock
from ..rendering.fragments import iter_text_units
from ..core.styles import (
    assistant_block,
    assistant_continuation_block,
    styled_block_fragments
)

MARKDOWN_H1_STYLE = TextStyle(bold=True, underline=True)
MARKDOWN_H2_STYLE = TextStyle(bold=True)
MARKDOWN_H3_STYLE = TextStyle(bold=True, italic=True)
MARKDOWN_H4_STYLE = TextStyle(dim=True, italic=True)

MARKDOWN_UNORDERED_MARKER_STYLE = TextStyle(dim=True)
MARKDOWN_ORDERED_MARKER_STYLE   = TextStyle(foreground="ansiblue")
MARKDOWN_QUOTE_MARKER_STYLE     = TextStyle(foreground="ansigreen", dim=True)
MARKDOWN_QUOTE_STYLE            = TextStyle(dim=True)
MARKDOWN_CODE_STYLE             = TextStyle(foreground="ansicyan")
MARKDOWN_LINK_STYLE             = TextStyle(foreground="ansicyan", underline=True)
MARKDOWN_TABLE_HEADER_STYLE     = TextStyle(foreground="ansiblue", bold=True)
MARKDOWN_SEPARATOR_STYLE        = TextStyle(dim=True)

TABLE_COLUMN_GAP                   = 2
TABLE_LEADING_PADDING              = 1
TABLE_MIN_COLUMN_WIDTH             = 3
TABLE_MIN_SCANNABLE_WIDTH          = 12
TABLE_MIN_ALIGNED_VALUE_WIDTH      = 12
TABLE_CRAMPED_CELL_LINES           = 4
TABLE_CATASTROPHIC_NARRATIVE_LINES = 7
TABLE_RECORD_FIELD_GAP             = 2
TABLE_RECORD_VALUE_INDENT          = 2

_MARKDOWN = MarkdownIt("commonmark").enable(("table", "strikethrough"))
_REFERENCE_PROBE = MarkdownIt(
    "commonmark",
    {"store_labels": True},
).enable(("table", "strikethrough"))

_MARKDOWN_TABLE_FENCE_OPEN = re.compile(
    r"^ {0,3}(?P<fence>`{3,}|~{3,})[ \t]*(?:md|markdown)[ \t]*$",
    re.IGNORECASE,
)

_PLAIN_STREAM_UNSAFE = re.compile(
    r"\r?\n\r?\n|^[ \t]|^\d{1,9}[.)][ \t]+|"
    r"[\\&<>`*_\[\]!~+#=|-]",
    re.MULTILINE,
)

_RenderedTableRow = tuple[bool, list[list[TextSpan]], list[str]]


class _ReferenceProbeDefinitions(dict[str, dict[str, str]]):
    """为引用语法探测提供任意标签的虚拟定义。"""

    def get(
        self,
        _key: str,
        _default: typing.Any = None,
    ) -> dict[str, str]:
        return {"href": "reference", "title": ""}


_REFERENCE_PROBE_DEFINITIONS = _ReferenceProbeDefinitions()


@dataclass
class _StreamingTableState:
    """保存流式表格已经解析完成的行。"""
    source: str
    header_source: str
    rows: list[_RenderedTableRow]
    layout_width: int | None
    natural_widths: list[int]
    fitted_widths: list[int]
    record_layout: bool
    column_kinds: list[str]
    affected_rows: int
    lines: list[list[TextSpan]]


class TuiMarkdownStreamRenderer(object):
    """增量渲染完整源码行并缓存已经稳定的顶层块。"""

    def __init__(self) -> None:
        self._width: int | None = None
        self._source: str       = ""

        self._stable_source_len: int                  = 0
        self._stable_lines: list[list[TextSpan]]      = []
        self._committable_source_len: int             = 0
        self._committable_lines: list[list[TextSpan]] = []
        self._stable_source_compatible: bool          = True
        self._has_reference_definitions: bool         = False

        self._streaming_table: _StreamingTableState | None = None

    def reset(self) -> None:
        """清空稳定块缓存。"""
        self._stable_source_len         = 0
        self._stable_lines              = []
        self._committable_source_len    = 0
        self._committable_lines         = []
        self._stable_source_compatible  = True
        self._has_reference_definitions = False
        self._source                    = ""
        self._streaming_table           = None

    def stable_prefix(self) -> tuple[int, FragmentBlock]:
        """返回可独立提交的稳定源码长度和渲染块。"""
        if (
            not self._stable_source_compatible
            or self._committable_source_len <= 0
            or not self._committable_lines
        ):
            return 0, FragmentBlock(())

        return self._committable_source_len, _fragment_block_from_lines(
            [list(line) for line in self._committable_lines],
            hyperlinks=False,
            sanitize=False,
        )

    def render(
        self,
        text: str,
        *,
        final: bool = False,
        hyperlinks: bool = False,
        width: int | None = None
    ) -> FragmentBlock:
        """渲染完整源码行，并只重复处理仍可能变化的末尾块。"""
        original_source = str(text or "")
        source          = _normalize_markdown_table_fences(original_source)

        source_compatible = source == original_source

        render_width = max(1, int(width)) if width is not None else None
        if render_width != self._width:
            self.reset()
            self._width = render_width

        if self._source and not source.startswith(self._source):
            self.reset()
            self._width = render_width
        self._source = source

        self._stable_source_compatible = source_compatible

        if final or self._has_reference_definitions:
            return self._render_full_document(
                source,
                final=final,
                hyperlinks=hyperlinks,
                width=render_width,
            )

        tail = source[self._stable_source_len:]
        if not final:
            table_lines = self._append_streaming_table(tail, width=render_width)
            if table_lines is not None:
                lines = [list(line) for line in self._stable_lines]
                _extend_rendered_lines(lines, table_lines)
                return _fragment_block_from_lines(
                    lines,
                    hyperlinks=hyperlinks,
                    sanitize=False,
                )
        else:
            self._streaming_table = None

        if _plain_stream_paragraph(tail):
            self._streaming_table = None
            if not self._stable_lines:
                text = tail.rstrip("\r\n")
                return FragmentBlock((("", text),)) if text else FragmentBlock(())
            lines = [list(line) for line in self._stable_lines]
            _extend_rendered_lines(lines, _plain_lines(tail))
            return _fragment_block_from_lines(
                lines,
                hyperlinks=hyperlinks,
                sanitize=False,
            )

        nodes, env = _parse_markdown_nodes(tail)

        if env.get("references"):
            return self._render_full_document(
                source,
                final=final,
                hyperlinks=hyperlinks,
                width=render_width,
            )

        if not final and len(nodes) == 1 and nodes[0].type == "table":
            table = nodes[0]
            header_source = _table_header_source(tail, table)
            if header_source is not None:
                rows = _table_rendered_rows(table)
                (
                    table_lines,
                    natural_widths,
                    fitted_widths,
                    record_layout,
                ) = _table_layout_from_rows(rows, width=render_width)
                _, column_kinds, affected_rows = _table_record_analysis(
                    rows,
                    widths=fitted_widths,
                    natural_widths=natural_widths,
                    width=render_width,
                )
                self._streaming_table = _StreamingTableState(
                    source=tail,
                    header_source=header_source,
                    rows=rows,
                    layout_width=render_width,
                    natural_widths=natural_widths,
                    fitted_widths=fitted_widths,
                    record_layout=record_layout,
                    column_kinds=column_kinds,
                    affected_rows=affected_rows,
                    lines=table_lines,
                )
                lines = [list(line) for line in self._stable_lines]
                _extend_rendered_lines(lines, table_lines)
                return _fragment_block_from_lines(
                    lines,
                    hyperlinks=hyperlinks,
                    sanitize=False,
                )

        self._streaming_table = None

        stable_count = _stable_node_count(tail, nodes, final=final)
        stable_end   = _stable_source_end(tail, nodes, stable_count)

        if stable_end:
            previous_stable_end = self._stable_source_len
            newly_stable = _render_blocks(
                nodes[:stable_count],
                width=render_width,
            )
            _extend_rendered_lines(self._stable_lines, newly_stable)
            self._stable_source_len += stable_end

            if self._committable_source_len == previous_stable_end:
                committable_count, committable_end = (
                    _committable_prefix_boundary(
                        tail,
                        nodes,
                        stable_count,
                        env=env,
                        final=final,
                    )
                )
                if committable_count:
                    committable_lines = (
                        newly_stable
                        if committable_count == stable_count
                        else _render_blocks(
                            nodes[:committable_count],
                            width=render_width,
                        )
                    )
                    _extend_rendered_lines(
                        self._committable_lines,
                        committable_lines,
                    )
                self._committable_source_len += committable_end

        lines = [list(line) for line in self._stable_lines]

        _extend_rendered_lines(
            lines,
            _render_blocks(
                nodes[stable_count:],
                width=render_width,
            ),
        )

        return _fragment_block_from_lines(
            lines,
            hyperlinks=hyperlinks,
            sanitize=False,
        )

    def _render_full_document(
        self,
        source: str,
        *,
        final: bool,
        hyperlinks: bool,
        width: int | None
    ) -> FragmentBlock:
        """完整渲染源码并重建不可逆的终端提交边界。"""
        nodes, env = _parse_markdown_nodes(source)

        stable_count = _stable_node_count(source, nodes, final=final)

        committable_count, committable_end = _committable_prefix_boundary(
            source,
            nodes,
            stable_count,
            env=env,
            final=final,
        )

        self._stable_source_len      = 0
        self._stable_lines           = []
        self._committable_source_len = committable_end

        self._committable_lines = _render_blocks(
            nodes[:committable_count],
            width=width,
        )

        self._streaming_table           = None
        self._has_reference_definitions = bool(env.get("references"))

        return _fragment_block_from_lines(
            _render_blocks(nodes, width=width),
            hyperlinks=hyperlinks,
            sanitize=False,
        )

    def _append_streaming_table(
        self,
        source: str,
        *,
        width: int | None
    ) -> list[list[TextSpan]] | None:
        """增量解析表格新增行并返回当前完整布局。"""
        state = self._streaming_table
        if state is None or not source.startswith(state.source):
            self._streaming_table = None
            return None

        suffix = source[len(state.source):]
        if suffix:
            appended = _parse_appended_table_rows(
                state.header_source,
                suffix,
            )
            if not appended:
                self._streaming_table = None
                return None
            had_body = any(not header for header, _cells, _alignments in state.rows)

            state.rows.extend(appended)
            state.source = source

            natural_widths = list(state.natural_widths)
            for _header, cells, _alignments in appended:
                if len(cells) > len(natural_widths):
                    natural_widths.extend([1] * (len(cells) - len(natural_widths)))
                for index, cell in enumerate(cells):
                    natural_widths[index] = max(
                        natural_widths[index],
                        _spans_width(cell),
                    )
            fitted_widths = _fit_table_widths(
                natural_widths,
                width=width,
            )

            layout_dimensions_unchanged = bool(
                width == state.layout_width
                and fitted_widths == state.fitted_widths
            )
            if layout_dimensions_unchanged:
                column_kinds = _table_column_kinds(
                    state.rows,
                    natural_widths=natural_widths,
                )
                if column_kinds == state.column_kinds:
                    affected_rows = state.affected_rows + sum(
                        _table_row_is_affected(
                            cells,
                            widths=fitted_widths,
                            kinds=column_kinds,
                        )
                        for header, cells, _alignments in appended
                        if not header
                    )
                    body_count = sum(
                        not header
                        for header, _cells, _alignments in state.rows
                    )
                    record_layout = _table_record_decision(
                        width=width,
                        widths=fitted_widths,
                        body_count=body_count,
                        affected_rows=affected_rows,
                    )
                else:
                    (
                        record_layout,
                        column_kinds,
                        affected_rows,
                    ) = _table_record_analysis(
                        state.rows,
                        widths=fitted_widths,
                        natural_widths=natural_widths,
                        width=width,
                    )
            else:
                record_layout = not state.record_layout
                column_kinds  = []
                affected_rows = 0

            can_append_layout = bool(
                layout_dimensions_unchanged
                and record_layout == state.record_layout
            )
            if can_append_layout:
                if record_layout:
                    _extend_table_record_lines(
                        state.lines,
                        state.rows,
                        appended,
                        width=width,
                        body_started=had_body,
                    )
                else:
                    _extend_table_grid_lines(
                        state.lines,
                        appended,
                        widths=fitted_widths,
                        body_started=had_body,
                    )

                state.natural_widths = natural_widths
                state.fitted_widths  = fitted_widths
                state.column_kinds   = column_kinds
                state.affected_rows  = affected_rows

            else:
                (
                    state.lines,
                    state.natural_widths,
                    state.fitted_widths,
                    state.record_layout,
                ) = _table_layout_from_rows(state.rows, width=width)
                state.layout_width = width
                (
                    _,
                    state.column_kinds,
                    state.affected_rows,
                ) = _table_record_analysis(
                    state.rows,
                    widths=state.fitted_widths,
                    natural_widths=state.natural_widths,
                    width=width,
                )

        elif width != state.layout_width:
            (
                state.lines,
                state.natural_widths,
                state.fitted_widths,
                state.record_layout,
            ) = _table_layout_from_rows(state.rows, width=width)
            state.layout_width = width
            (
                _,
                state.column_kinds,
                state.affected_rows,
            ) = _table_record_analysis(
                state.rows,
                widths=state.fitted_widths,
                natural_widths=state.natural_widths,
                width=width,
            )

        return state.lines


def render_tui_markdown(
    text: str,
    *,
    hyperlinks: bool = False,
    width: int | None = None
) -> FragmentBlock:
    """把完整 assistant Markdown 原文转换为 TUI 文本片段。"""
    spans = _markdown_spans(
        _normalize_markdown_table_fences(str(text or "")),
        width=width,
    )
    return _fragment_block_from_spans(spans, hyperlinks=hyperlinks)


def render_tui_assistant_markdown(
    text: str,
    width: int,
    *,
    hyperlinks: bool = False,
    continuation: bool = False
) -> FragmentBlock:
    """按完整终端宽度渲染可重排的助手 Markdown 正文或续块。"""
    try:
        rendered = render_tui_markdown(
            text,
            hyperlinks=hyperlinks,
            width=max(1, int(width) - 2),
        )
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        rendered = FragmentBlock(styled_block_fragments(
            StyledBlock(plain_text=str(text or "")),
        ))

    renderer = (
        assistant_continuation_block
        if continuation
        else assistant_block
    )

    return renderer(rendered)


def _normalize_markdown_table_fences(source: str) -> str:
    """把只包含原生表格的 Markdown 围栏转换为可渲染表格。"""
    if "```" not in source and "~~~" not in source:
        return source

    lines = source.splitlines(keepends=True)

    out: list[str] = []

    index: int = 0
    while index < len(lines):
        opening_text = lines[index].rstrip("\r\n")

        opening = _MARKDOWN_TABLE_FENCE_OPEN.fullmatch(opening_text)
        if opening is None:
            out.append(lines[index])
            index += 1
            continue

        fence = opening.group("fence")

        closing = _markdown_fence_close_index(
            lines,
            start=index + 1,
            marker=fence[0],
            minimum=len(fence),
        )
        if closing is None:
            out.append(lines[index])
            index += 1
            continue

        body = "".join(lines[index + 1:closing]).strip("\r\n")
        root = SyntaxTreeNode(_MARKDOWN.parse(body))

        if len(root.children) == 1 and root.children[0].type == "table":
            out.append(body)
            if lines[closing].endswith(("\n", "\r")):
                out.append("\n")
        else:
            out.extend(lines[index:closing + 1])

        index = closing + 1

    return "".join(out)


def _markdown_fence_close_index(
    lines: list[str],
    *,
    start: int,
    marker: str,
    minimum: int
) -> int | None:
    """返回符合 CommonMark 长度规则的围栏关闭行位置。"""
    for index in range(start, len(lines)):
        candidate = lines[index].rstrip("\r\n")
        stripped  = candidate.lstrip(" ")

        if len(candidate) - len(stripped) > 3:
            continue

        count = len(stripped) - len(stripped.lstrip(marker))
        if count >= minimum and not stripped[count:].strip():
            return index

    return None


def _plain_stream_paragraph(source: str) -> bool:
    """判断可否跳过块解析并按普通源码行直接生成流式快照。"""
    return bool(source and _PLAIN_STREAM_UNSAFE.search(source) is None)


def _fragment_block_from_lines(
    lines: list[list[TextSpan]],
    *,
    hyperlinks: bool,
    sanitize: bool = True
) -> FragmentBlock:
    """把 Markdown 渲染行转换为 TUI 片段块。"""
    while lines and not lines[-1]:
        lines.pop()

    parts: list[TextSpan] = []
    for index, line in enumerate(lines):
        if index:
            parts.append(TextSpan("\n", TextStyle()))
        parts.extend(line)

    spans = _coalesce_spans(parts)

    return _fragment_block_from_spans(
        spans,
        hyperlinks=hyperlinks,
        sanitize=sanitize,
    )


def _coalesce_spans(spans: list[TextSpan]) -> list[TextSpan]:
    """在线性时间内合并相邻同样式 Markdown 片段。"""
    if not spans:
        return []

    out: list[TextSpan] = []

    run_style     = spans[0].style
    run_hyperlink = spans[0].hyperlink

    run_text: list[str] = []

    for span in spans:
        if span.style == run_style and span.hyperlink == run_hyperlink:
            run_text.append(span.text)
            continue
        out.append(TextSpan("".join(run_text), run_style, run_hyperlink))
        run_style = span.style
        run_hyperlink = span.hyperlink
        run_text = [span.text]

    out.append(TextSpan("".join(run_text), run_style, run_hyperlink))
    return out


def _fragment_block_from_spans(
    spans: list[TextSpan],
    *,
    hyperlinks: bool,
    sanitize: bool = True
) -> FragmentBlock:
    """把 Markdown 样式片段转换为 TUI 片段块。"""
    plain_text = "".join(span.text for span in spans)

    block = StyledBlock(
        plain_text=plain_text,
        spans=tuple(spans),
    )
    if sanitize:
        block = sanitize_styled_block(block)

    return FragmentBlock(styled_block_fragments(
        block,
        hyperlinks=hyperlinks,
    ))


def _markdown_spans(
    text: str,
    *,
    width: int | None = None
) -> list[TextSpan]:
    """把 Markdown 文本解析为中立样式片段。"""
    root = SyntaxTreeNode(_MARKDOWN.parse(str(text or "")))

    lines = _render_blocks(root.children, width=width)
    while lines and not lines[-1]:
        lines.pop()

    spans: list[TextSpan] = []
    for index, line in enumerate(lines):
        if index:
            _append_span(spans, "\n", TextStyle())
        _extend_spans(spans, line)

    return spans


def _parse_markdown_nodes(
    source: str
) -> tuple[list[SyntaxTreeNode], dict[str, typing.Any]]:
    """解析 Markdown 顶层节点并返回解析环境。"""
    env: dict[str, typing.Any] = {}
    root = SyntaxTreeNode(_MARKDOWN.parse(source, env))
    return root.children, env


def _committable_prefix_boundary(
    source: str,
    nodes: list[SyntaxTreeNode],
    stable_count: int,
    *,
    env: dict[str, typing.Any],
    final: bool
) -> tuple[int, int]:
    """返回允许写入终端历史的稳定节点数量和源码边界。"""
    stable_count = max(0, min(int(stable_count), len(nodes)))
    stable_end   = _stable_source_end(source, nodes, stable_count)

    if final or stable_count <= 0:
        return stable_count, stable_end

    offsets           = _line_start_offsets(source)
    definition_start  = _reference_definition_start(env, offsets)
    committable_count = 0
    boundary          = definition_start

    for node in nodes[:stable_count]:
        node_start = _node_source_start(node, offsets)
        if boundary is not None and (
            node_start is None or node_start >= boundary
        ):
            break
        if _node_has_reference_syntax(node):
            boundary = 0 if node_start is None else node_start
            break
        committable_count += 1

    committable_end = _stable_source_end(
        source,
        nodes,
        committable_count,
    )
    if boundary is not None:
        committable_end = min(committable_end, boundary)

    return committable_count, committable_end


def _reference_definition_start(
    env: dict[str, typing.Any],
    offsets: list[int]
) -> int | None:
    """返回解析环境中最早引用定义的源码位置。"""
    references = env.get("references")
    if not isinstance(references, dict):
        return None

    starts: list[int] = []

    for reference in references.values():
        if not isinstance(reference, dict):
            continue
        source_map = reference.get("map")
        if (
            not isinstance(source_map, (list, tuple))
            or not source_map
            or not isinstance(source_map[0], int)
        ):
            continue
        line = source_map[0]
        if 0 <= line < len(offsets):
            starts.append(offsets[line])

    return min(starts) if starts else None


def _node_source_start(
    node: SyntaxTreeNode,
    offsets: list[int]
) -> int | None:
    """返回顶层节点的源码起始位置。"""
    source_map = node.map
    if source_map is None:
        return None
    line = source_map[0]
    if line < 0 or line >= len(offsets):
        return None
    return offsets[line]


def _node_has_reference_syntax(node: SyntaxTreeNode) -> bool:
    """判断节点是否包含 CommonMark 引用链接或引用图片语法。"""
    for child in node.walk(include_self=True):
        if child.type != "inline":
            continue
        # 由解析器识别语法有效的任意引用，避免复制 CommonMark 括号规则。
        probe_env: dict[str, typing.Any] = {
            "references": _REFERENCE_PROBE_DEFINITIONS,
        }
        tokens = _REFERENCE_PROBE.parseInline(child.content, probe_env)
        for token in tokens:
            for inline in token.children or ():
                if inline.meta.get("label"):
                    return True
    return False


def _stable_node_count(
    source: str,
    nodes: list[SyntaxTreeNode],
    *,
    final: bool
) -> int:
    """返回不再受后续源码行影响的顶层块数量。"""
    if final or not nodes:
        return len(nodes)

    last = nodes[-1]

    if last.type in {"heading", "hr"} or _closed_fence(source, last):
        return len(nodes)

    return max(0, len(nodes) - 1)


def _stable_source_end(
    source: str,
    nodes: list[SyntaxTreeNode],
    stable_count: int
) -> int:
    """返回稳定顶层块对应的源码结束位置。"""
    if stable_count <= 0:
        return 0
    if stable_count >= len(nodes):
        return len(source)

    line_map = nodes[stable_count].map
    if line_map is None:
        return 0

    return _line_start_offsets(source)[line_map[0]]


def _line_start_offsets(source: str) -> list[int]:
    """返回每一源码行的起始字符位置。"""
    offsets = [0]
    for index, char in enumerate(source):
        if char == "\n":
            offsets.append(index + 1)
    return offsets


def _closed_fence(source: str, node: SyntaxTreeNode) -> bool:
    """判断围栏代码块是否已经收到合法关闭行。"""
    if node.type != "fence" or not node.markup or node.map is None:
        return False

    lines = source.splitlines()

    end_line = node.map[1] - 1
    if end_line < 0 or end_line >= len(lines):
        return False

    candidate = lines[end_line]
    stripped  = candidate.lstrip(" ")

    if len(candidate) - len(stripped) > 3:
        return False

    marker = node.markup[0]
    count  = len(stripped) - len(stripped.lstrip(marker))

    return bool(
        count >= len(node.markup)
        and not stripped[count:].strip()
    )


def _extend_rendered_lines(
    target: list[list[TextSpan]],
    source: list[list[TextSpan]]
) -> None:
    """按顶层块间距规则拼接两组 Markdown 渲染行。"""
    if not source:
        return None
    if target and target[-1] and source[0]:
        target.append([])
    target.extend(source)


def _render_blocks(
    nodes: list[SyntaxTreeNode],
    *,
    list_depth: int = 0,
    width: int | None = None
) -> list[list[TextSpan]]:
    """渲染一组块级 Markdown 节点。"""
    lines: list[list[TextSpan]] = []

    for node in nodes:
        rendered = _render_block(
            node,
            list_depth=list_depth,
            width=width,
        )
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
    width: int | None = None,
) -> list[list[TextSpan]]:
    """渲染一个块级 Markdown 节点。"""
    if node.type == "paragraph":
        return _inline_lines(node.children)
    if node.type == "heading":
        return _heading_lines(node)
    if node.type in {"fence", "code_block"}:
        return _code_lines(node.content, language=node.info)
    if node.type == "bullet_list":
        return _list_lines(
            node,
            ordered=False,
            depth=list_depth,
            width=width,
        )
    if node.type == "ordered_list":
        return _list_lines(
            node,
            ordered=True,
            depth=list_depth,
            width=width,
        )
    if node.type == "blockquote":
        return _blockquote_lines(
            node,
            list_depth=list_depth,
            width=width,
        )
    if node.type == "table":
        return _table_lines(node, width=width)
    if node.type == "hr":
        return [[TextSpan("———", MARKDOWN_SEPARATOR_STYLE)]]
    if node.type in {"html_block", "text"}:
        return _plain_lines(node.content)
    if node.type == "inline":
        return _inline_lines([node])

    return _render_blocks(
        node.children,
        list_depth=list_depth,
        width=width,
    )


def _heading_lines(node: SyntaxTreeNode) -> list[list[TextSpan]]:
    """渲染保留 Markdown 层级标记的标题行。"""
    level = (
        int(node.tag[1:])
        if node.tag.startswith("h") and node.tag[1:].isdigit()
        else 2
    )
    style = (
        MARKDOWN_H1_STYLE
        if level == 1
        else MARKDOWN_H2_STYLE
        if level == 2
        else MARKDOWN_H3_STYLE
        if level == 3
        else MARKDOWN_H4_STYLE
    )

    prefix = f"{'#' * level} "
    lines  = _inline_lines(node.children, base_style=style)

    return [
        [
            TextSpan(prefix if index == 0 else " " * len(prefix), style),
            *line,
        ]
        for index, line in enumerate(lines)
    ]


def _list_lines(
    node: SyntaxTreeNode,
    *,
    ordered: bool,
    depth: int,
    width: int | None
) -> list[list[TextSpan]]:
    """渲染有序或无序列表。"""
    lines: list[list[TextSpan]] = []

    start  = int(node.attrs.get("start") or 1) if ordered else 1
    indent = "    " * depth

    for index, item in enumerate(node.children):
        marker       = f"{start + index}. " if ordered else "- "
        marker_width = len(marker)

        marker_style = (
            MARKDOWN_ORDERED_MARKER_STYLE
            if ordered
            else MARKDOWN_UNORDERED_MARKER_STYLE
        )

        item_lines: list[list[TextSpan]]   = []
        nested_lines: list[list[TextSpan]] = []

        for child in item.children:
            if child.type in {"bullet_list", "ordered_list"}:
                nested_lines.extend(_render_block(
                    child,
                    list_depth=depth + 1,
                    width=width,
                ))
                continue
            rendered = _render_block(
                child,
                list_depth=depth + 1,
                width=width,
            )
            if item_lines and item_lines[-1] and rendered and rendered[0]:
                item_lines.append([])
            item_lines.extend(rendered)

        if not item_lines:
            item_lines = [[]]

        item_start    = len(lines)
        first_content = True

        for line in item_lines:
            if line and first_content:
                lines.extend(_prefixed_wrapped_lines(
                    line,
                    prefix=[
                        TextSpan(indent, TextStyle()),
                        TextSpan(marker, marker_style),
                    ],
                    width=width,
                ))
                first_content = False
            elif line:
                lines.extend(_prefixed_wrapped_lines(
                    line,
                    prefix=[TextSpan(
                        f"{indent}{' ' * marker_width}",
                        TextStyle(),
                    )],
                    width=width,
                ))
            else:
                lines.append([])

        lines.extend(nested_lines)
        if (
            index + 1 < len(node.children)
            and len(lines) - item_start > 1
            and lines[-1]
        ):
            lines.append([])

    return lines


def _prefixed_wrapped_lines(
    line: list[TextSpan],
    *,
    prefix: list[TextSpan],
    width: int | None
) -> list[list[TextSpan]]:
    """按前缀占用宽度折行，并让后续行与正文起点对齐。"""
    prefix_width = _spans_width(prefix)

    wrapped = (
        _wrap_spans(line, width=max(1, width - prefix_width))
        if width is not None
        else [line]
    )

    return [
        [
            *(prefix if index == 0 else [TextSpan(" " * prefix_width)]),
            *part,
        ]
        for index, part in enumerate(wrapped)
    ]


def _blockquote_lines(
    node: SyntaxTreeNode,
    *,
    list_depth: int,
    width: int | None
) -> list[list[TextSpan]]:
    """渲染引用块并保留引用内换行。"""
    content = _render_blocks(
        node.children,
        list_depth=list_depth,
        width=(max(1, width - 2) if width is not None else None),
    )

    return [
        [TextSpan("▎ ", MARKDOWN_QUOTE_MARKER_STYLE), *(
            _apply_style(line, MARKDOWN_QUOTE_STYLE) if line else []
        )]
        for line in content
    ]


def _table_lines(
    node: SyntaxTreeNode,
    *,
    width: int | None
) -> list[list[TextSpan]]:
    """按当前宽度把表格渲染为轻量网格或键值记录。"""
    return _table_lines_from_rows(
        _table_rendered_rows(node),
        width=width,
    )


def _table_rendered_rows(node: SyntaxTreeNode) -> list[_RenderedTableRow]:
    """把表格节点转换为可重复排版的单元格行。"""
    rows: list[tuple[bool, list[SyntaxTreeNode]]] = []
    for section in node.children:
        header = section.type == "thead"
        for row in section.children:
            if row.type == "tr":
                rows.append((header, row.children))

    if not rows:
        return []

    column_count = max(len(cells) for _header, cells in rows)

    rendered_rows: list[_RenderedTableRow] = []

    for header, cells in rows:
        rendered_cells: list[list[TextSpan]] = []
        alignments: list[str] = []
        for index in range(column_count):
            if index < len(cells):
                cell = cells[index]
                spans = _table_cell_spans(cell)
                alignment = _table_alignment(cell)
            else:
                spans = []
                alignment = "left"
            rendered_cells.append(spans)
            alignments.append(alignment)
        rendered_rows.append((header, rendered_cells, alignments))

    return rendered_rows


def _table_lines_from_rows(
    rendered_rows: list[_RenderedTableRow],
    *,
    width: int | None
) -> list[list[TextSpan]]:
    """按当前宽度排版已经解析完成的表格行。"""
    lines, _natural, _fitted, _records = _table_layout_from_rows(
        rendered_rows,
        width=width,
    )
    return lines


def _table_layout_from_rows(
    rendered_rows: list[_RenderedTableRow],
    *,
    width: int | None
) -> tuple[list[list[TextSpan]], list[int], list[int], bool]:
    """排版表格并返回可供流式增量复用的布局信息。"""
    if not rendered_rows:
        return [], [], [], False

    column_count = max(len(cells) for _header, cells, _alignments in rendered_rows)
    widths       = [1] * column_count

    for _header, cells, _alignments in rendered_rows:
        for index, spans in enumerate(cells):
            widths[index] = max(widths[index], _spans_width(spans))

    natural_widths = list(widths)
    widths         = _fit_table_widths(widths, width=width)

    record_layout = _table_should_render_records(
        rendered_rows,
        widths=widths,
        natural_widths=natural_widths,
        width=width,
    )
    lines = (
        _table_record_lines(rendered_rows, width=width)
        if record_layout
        else _table_grid_lines(rendered_rows, widths=widths)
    )

    return lines, natural_widths, widths, record_layout


def _table_header_source(
    source: str,
    node: SyntaxTreeNode,
) -> str | None:
    """返回可用于独立解析新增表格行的表头源码。"""
    if node.map is None:
        return None

    start = node.map[0]
    lines = source.splitlines(keepends=True)

    if start < 0 or start + 1 >= len(lines):
        return None

    header = "".join(lines[start:start + 2])
    if not header.endswith(("\n", "\r")):
        header += "\n"
    return header


def _parse_appended_table_rows(
    header_source: str,
    suffix: str
) -> list[_RenderedTableRow] | None:
    """解析追加到既有表格尾部的新数据行。"""
    candidate = header_source + suffix
    root      = SyntaxTreeNode(_MARKDOWN.parse(candidate))

    if len(root.children) != 1 or root.children[0].type != "table":
        return None

    table = root.children[0]
    if table.map is None or table.map[1] != len(candidate.splitlines()):
        return None

    rows      = _table_rendered_rows(table)
    body_rows = [row for row in rows if not row[0]]

    return body_rows or None


def _table_cell_spans(cell: SyntaxTreeNode) -> list[TextSpan]:
    """返回单个表格单元格的内联片段。"""
    lines = _inline_lines(cell.children)
    spans: list[TextSpan] = []

    for index, line in enumerate(lines):
        if index:
            _append_span(spans, " ", TextStyle())
        _extend_spans(spans, line)

    return spans


def _table_alignment(cell: SyntaxTreeNode) -> str:
    """返回表格单元格声明的对齐方式。"""
    style = str(cell.attrs.get("style") or "")
    if style.endswith(":right"):
        return "right"
    if style.endswith(":center"):
        return "center"

    return "left"


def _spans_width(spans: list[TextSpan]) -> int:
    """返回中立样式片段占用的终端列数。"""
    return max(0, get_cwidth("".join(span.text for span in spans)))


def _fit_table_widths(
    natural_widths: list[int],
    *,
    width: int | None
) -> list[int]:
    """把表格列宽压缩到当前终端内容宽度。"""
    widths = [max(1, value) for value in natural_widths]
    if width is None:
        return widths

    content_budget = max(
        len(widths),
        int(width)
        - TABLE_LEADING_PADDING
        - TABLE_COLUMN_GAP * max(0, len(widths) - 1),
    )
    if sum(widths) <= content_budget:
        return widths

    low, high = 1, max(widths)

    while low < high:
        cap = (low + high + 1) // 2
        if sum(min(value, cap) for value in widths) <= content_budget:
            low = cap
        else:
            high = cap - 1

    fitted    = [min(value, low) for value in widths]
    remaining = content_budget - sum(fitted)

    for index, natural in enumerate(widths):
        if remaining <= 0:
            break
        growth = min(remaining, natural - fitted[index])
        fitted[index] += growth
        remaining -= growth

    return fitted


def _wrap_spans(
    spans: list[TextSpan],
    *,
    width: int
) -> list[list[TextSpan]]:
    """优先在词边界折行，并保留样式与链接边界。"""
    limit      = max(1, int(width))
    plain_text = "".join(span.text for span in spans)

    if (
        _spans_width(spans) <= limit
        and (
            not plain_text
            or (
                not plain_text[0].isspace()
                and not plain_text[-1].isspace()
            )
        )
    ):
        return [list(spans)]

    lines: list[list[TextSpan]] = []
    current: list[TextSpan]     = []

    used: int = 0

    last_space: int | None = None

    for span in spans:
        for unit in iter_text_units(span.text):
            unit_width = max(0, get_cwidth(unit))
            if current and unit_width and used + unit_width > limit:
                if last_space is not None:

                    before = current[:last_space]
                    after  = current[last_space + 1:]

                    lines.append(_merge_text_span_units(before))

                    current = after
                    used    = sum(_spans_width([item]) for item in current)

                else:
                    lines.append(_merge_text_span_units(current))

                    current = []
                    used    = 0

                last_space = next((
                    index
                    for index in range(len(current) - 1, -1, -1)
                    if current[index].text.isspace()
                ), None)

            if not current and unit.isspace():
                continue
            current.append(TextSpan(unit, span.style, span.hyperlink))
            used += unit_width
            if unit.isspace():
                last_space = len(current) - 1

    while current and current[-1].text.isspace():
        current.pop()
    lines.append(_merge_text_span_units(current))

    return lines or [[]]


def _merge_text_span_units(units: list[TextSpan]) -> list[TextSpan]:
    """合并折行过程中产生的相邻同样式文本单元。"""
    merged: list[TextSpan] = []
    for unit in units:
        _append_span(merged, unit.text, unit.style, unit.hyperlink)
    return merged


def _table_grid_lines(
    rows: list[tuple[bool, list[list[TextSpan]], list[str]]],
    *,
    widths: list[int]
) -> list[list[TextSpan]]:
    """生成无竖线、带轻量行分隔的表格网格。"""
    lines: list[list[TextSpan]] = []

    grid_width = (
        TABLE_LEADING_PADDING
        + sum(widths)
        + TABLE_COLUMN_GAP * max(0, len(widths) - 1)
    )

    body_started: bool = False

    for header, cells, alignments in rows:
        if header:
            pass
        elif not body_started:
            lines.append(_table_separator(grid_width, character="━"))
            body_started = True
        elif lines:
            lines.append(_table_separator(grid_width, character="─"))

        lines.extend(_table_grid_data_lines(
            cells,
            alignments,
            widths=widths,
            header=header,
        ))

    if rows and not body_started:
        lines.append(_table_separator(grid_width, character="━"))
    return lines


def _extend_table_grid_lines(
    lines: list[list[TextSpan]],
    rows: list[_RenderedTableRow],
    *,
    widths: list[int],
    body_started: bool
) -> None:
    """在列宽不变时只把新增数据行追加到既有网格。"""
    grid_width = (
        TABLE_LEADING_PADDING
        + sum(widths)
        + TABLE_COLUMN_GAP * max(0, len(widths) - 1)
    )

    for header, cells, alignments in rows:
        if not header:
            if body_started:
                lines.append(_table_separator(grid_width, character="─"))
            body_started = True
        lines.extend(_table_grid_data_lines(
            cells,
            alignments,
            widths=widths,
            header=header,
        ))


def _table_grid_data_lines(
    cells: list[list[TextSpan]],
    alignments: list[str],
    *,
    widths: list[int],
    header: bool
) -> list[list[TextSpan]]:
    """生成一个表格数据行折行后的全部网格行。"""
    wrapped_cells = [
        _wrap_spans(cell, width=widths[index])
        for index, cell in enumerate(cells)
    ]

    row_height = max(len(cell_lines) for cell_lines in wrapped_cells)

    return [
        _table_grid_row(
            [
                cell_lines[line_index]
                if line_index < len(cell_lines)
                else []
                for cell_lines in wrapped_cells
            ],
            widths,
            alignments,
            header=header,
        )
        for line_index in range(row_height)
    ]


def _table_grid_row(
    cells: list[list[TextSpan]],
    widths: list[int],
    alignments: list[str],
    *,
    header: bool
) -> list[TextSpan]:
    """生成一条保留单元格样式和对齐方式的网格行。"""
    out = [TextSpan(" " * TABLE_LEADING_PADDING)]

    for index, width in enumerate(widths):

        spans     = cells[index]
        used      = _spans_width(spans)
        padding   = max(0, width - used)
        alignment = alignments[index]

        if alignment == "right":
            left_padding, right_padding = padding, 0
        elif alignment == "center":
            left_padding  = padding // 2
            right_padding = padding - left_padding
        else:
            left_padding, right_padding = 0, padding

        _append_span(out, " " * left_padding, TextStyle())

        _extend_spans(
            out,
            _apply_style(spans, MARKDOWN_TABLE_HEADER_STYLE)
            if header
            else spans,
        )

        _append_span(out, " " * right_padding, TextStyle())

        if index < len(widths) - 1:
            _append_span(out, " " * TABLE_COLUMN_GAP, TextStyle())

    return out


def _table_separator(width: int, *, character: str) -> list[TextSpan]:
    """生成使用终端默认前景色的低对比度表格分隔线。"""
    return [TextSpan(
        character * max(1, int(width)),
        MARKDOWN_SEPARATOR_STYLE,
    )]


def _table_should_render_records(
    rows: list[tuple[bool, list[list[TextSpan]], list[str]]],
    *,
    widths: list[int],
    natural_widths: list[int],
    width: int | None
) -> bool:
    """判断压缩后的网格是否已经失去可扫描性。"""
    decision, _kinds, _affected = _table_record_analysis(
        rows,
        widths=widths,
        natural_widths=natural_widths,
        width=width,
    )
    return decision


def _table_record_analysis(
    rows: list[_RenderedTableRow],
    *,
    widths: list[int],
    natural_widths: list[int],
    width: int | None
) -> tuple[bool, list[str], int]:
    """返回表格记录布局判定及可增量维护的统计信息。"""
    kinds = _table_column_kinds(rows, natural_widths=natural_widths)
    if width is None or len(widths) <= 1:
        return False, kinds, 0

    if widths == natural_widths:
        return False, kinds, 0

    body_rows = [cells for header, cells, _alignments in rows if not header]
    if not body_rows:
        return False, kinds, 0

    affected_rows = sum(
        _table_row_is_affected(cells, widths=widths, kinds=kinds)
        for cells in body_rows
    )
    return (
        _table_record_decision(
            width=width,
            widths=widths,
            body_count=len(body_rows),
            affected_rows=affected_rows,
        ),
        kinds,
        affected_rows,
    )


def _table_record_decision(
    *,
    width: int | None,
    widths: list[int],
    body_count: int,
    affected_rows: int
) -> bool:
    """根据网格宽度和受影响行数决定是否使用记录布局。"""
    if width is None or len(widths) <= 1 or body_count <= 0:
        return False

    minimum_grid_width = (
        TABLE_LEADING_PADDING
        + TABLE_MIN_COLUMN_WIDTH * len(widths)
        + TABLE_COLUMN_GAP * max(0, len(widths) - 1)
    )
    if width < minimum_grid_width:
        return True

    threshold = 1 if body_count == 1 else max(2, (body_count + 2) // 3)
    return affected_rows >= threshold


def _table_row_is_affected(
    cells: list[list[TextSpan]],
    *,
    widths: list[int],
    kinds: list[str]
) -> bool:
    """判断一条数据行在压缩网格中是否已经难以阅读。"""
    fragmented: bool             = False
    starved_expansive: int       = 0
    catastrophic_narrative: bool = False

    for cell, column_width, kind in zip(cells, widths, kinds):
        text = "".join(span.text for span in cell)

        has_fragmented_token = any(
            get_cwidth(token) > column_width
            for token in text.split()
        )

        wrapped_height = len(_wrap_spans(cell, width=column_width))

        if (
            has_fragmented_token
            and kind != "narrative"
            and column_width < TABLE_MIN_SCANNABLE_WIDTH
        ):
            fragmented = True
        if (
            kind != "compact"
            and wrapped_height >= TABLE_CRAMPED_CELL_LINES
        ):
            starved_expansive += 1
        if (
            kind == "narrative"
            and column_width < TABLE_MIN_SCANNABLE_WIDTH
            and wrapped_height >= TABLE_CATASTROPHIC_NARRATIVE_LINES
        ):
            catastrophic_narrative = True

    return bool(
        fragmented
        or starved_expansive >= 2
        or catastrophic_narrative
    )


def _table_column_kinds(
    rows: list[tuple[bool, list[list[TextSpan]], list[str]]],
    *,
    natural_widths: list[int]
) -> list[str]:
    """按正文密度和长 token 特征分类表格列。"""
    kinds: list[str] = []

    for index, natural_width in enumerate(natural_widths):
        values = [
            "".join(span.text for span in cells[index]).strip()
            for _header, cells, _alignments in rows
            if index < len(cells)
        ]

        body_values   = values[1:] or values
        word_counts   = [len(value.split()) for value in body_values]
        average_words = sum(word_counts) / max(1, len(word_counts))

        average_width = sum(
            get_cwidth(value) for value in body_values
        ) / max(1, len(body_values))

        if average_words >= 4 or average_width >= 28:
            kinds.append("narrative")
        elif natural_width >= TABLE_MIN_SCANNABLE_WIDTH and any(
            _looks_like_long_token(value) for value in body_values
        ):
            kinds.append("token")
        else:
            kinds.append("compact")

    return kinds


def _looks_like_long_token(text: str) -> bool:
    """判断单元格是否主要由路径、URL 或哈希类长 token 构成。"""
    return any(
        get_cwidth(token) >= TABLE_MIN_SCANNABLE_WIDTH
        and (
            "://" in token
            or "/" in token
            or "\\" in token
            or bool(re.fullmatch(r"[0-9a-fA-F]{12,}", token))
        )
        for token in text.split()
    )


def _table_record_lines(
    rows: list[tuple[bool, list[list[TextSpan]], list[str]]],
    *,
    width: int | None
) -> list[list[TextSpan]]:
    """把窄屏表格转置为按记录排列的键值字段。"""
    headers   = next((cells for header, cells, _alignments in rows if header), [])
    body_rows = [cells for header, cells, _alignments in rows if not header]

    if not headers or not body_rows:
        natural = [max(1, _spans_width(cell)) for cell in headers]
        return _table_grid_lines(rows, widths=natural)

    available   = max(1, int(width)) if width is not None else None
    label_width = max(_spans_width(header) for header in headers)

    aligned = bool(
        available is None
        or (
            TABLE_LEADING_PADDING
            + label_width
            + TABLE_RECORD_FIELD_GAP
            + TABLE_MIN_ALIGNED_VALUE_WIDTH
            <= available
        )
    )
    lines: list[list[TextSpan]] = []

    for row_index, values in enumerate(body_rows):
        if row_index:
            separator_width = available or max(
                (_spans_width(line) for line in lines),
                default=1,
            )
            lines.append(_table_separator(separator_width, character="─"))

        for header, value in zip(headers, values):
            if aligned:
                lines.extend(_table_aligned_field_lines(
                    header,
                    value,
                    label_width=label_width,
                    width=available,
                ))
            else:
                lines.extend(_table_stacked_field_lines(
                    header,
                    value,
                    width=available,
                ))
    return lines


def _extend_table_record_lines(
    lines: list[list[TextSpan]],
    rows: list[_RenderedTableRow],
    appended: list[_RenderedTableRow],
    *,
    width: int | None,
    body_started: bool
) -> None:
    """在记录布局不变时只追加新产生的数据记录。"""
    headers = next((row for row in rows if row[0]), None)
    if headers is None:
        return None

    incremental_rows = [headers, *appended]
    appended_lines   = _table_record_lines(incremental_rows, width=width)

    if body_started and appended_lines:
        separator_width = max(1, int(width)) if width is not None else max(
            (_spans_width(line) for line in lines),
            default=1,
        )
        lines.append(_table_separator(separator_width, character="─"))
    lines.extend(appended_lines)


def _table_aligned_field_lines(
    header: list[TextSpan],
    value: list[TextSpan],
    *,
    label_width: int,
    width: int | None
) -> list[list[TextSpan]]:
    """生成标签和值横向对齐的键值字段。"""
    indent = TABLE_LEADING_PADDING + label_width + TABLE_RECORD_FIELD_GAP

    value_width = (
        max(1, width - indent)
        if width is not None
        else max(1, _spans_width(value))
    )

    wrapped = _wrap_spans(value, width=value_width)

    lines: list[list[TextSpan]] = []

    for index, part in enumerate(wrapped):
        if index:
            prefix = [TextSpan(" " * indent)]
        else:
            prefix = [TextSpan(" " * TABLE_LEADING_PADDING)]
            _extend_spans(
                prefix,
                _apply_style(header, MARKDOWN_TABLE_HEADER_STYLE),
            )
            prefix.append(TextSpan(
                " " * (
                    label_width
                    - _spans_width(header)
                    + TABLE_RECORD_FIELD_GAP
                )
            ))
        lines.append([*prefix, *part])

    return lines


def _table_stacked_field_lines(
    header: list[TextSpan],
    value: list[TextSpan],
    *,
    width: int | None
) -> list[list[TextSpan]]:
    """生成标签和值上下排列的窄屏键值字段。"""
    label_width = max(1, (width or _spans_width(header)) - TABLE_LEADING_PADDING)
    value_width = max(1, (width or _spans_width(value)) - TABLE_RECORD_VALUE_INDENT)

    labels = _wrap_spans(
        _apply_style(header, MARKDOWN_TABLE_HEADER_STYLE),
        width=label_width,
    )

    values = _wrap_spans(value, width=value_width)

    return [
        *[
            [TextSpan(" " * TABLE_LEADING_PADDING), *line]
            for line in labels
        ],
        *[
            [TextSpan(" " * TABLE_RECORD_VALUE_INDENT), *line]
            for line in values
        ],
    ]


def _inline_lines(
    nodes: list[SyntaxTreeNode],
    *,
    base_style: TextStyle = TextStyle()
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
    hyperlink: str | None = None
) -> list[TextSpan]:
    """递归渲染内联 Markdown 节点。"""
    spans: list[TextSpan] = []
    for node in nodes:
        if node.type == "text":
            _append_span(spans, node.content, style, hyperlink)
        elif node.type in {"softbreak", "hardbreak"}:
            _append_span(spans, "\n", style, hyperlink)
        elif node.type == "code_inline":
            _append_span(
                spans,
                node.content,
                _merge_style(style, MARKDOWN_CODE_STYLE),
                hyperlink,
            )
        elif node.type == "strong":
            _extend_spans(spans, _inline_spans(
                node.children,
                replace(style, bold=True),
                hyperlink,
            ))
        elif node.type == "em":
            _extend_spans(spans, _inline_spans(
                node.children,
                replace(style, italic=True),
                hyperlink,
            ))
        elif node.type == "s":
            _extend_spans(spans, _inline_spans(
                node.children,
                replace(style, strikethrough=True),
                hyperlink,
            ))
        elif node.type == "link":
            _extend_spans(spans, _inline_spans(
                node.children,
                _merge_style(style, MARKDOWN_LINK_STYLE),
                str(node.attrs.get("href") or "") or None,
            ))
        elif node.type == "image":
            alt = node.attrs.get("alt") or node.content
            _append_span(
                spans,
                str(alt or ""),
                _merge_style(style, MARKDOWN_LINK_STYLE),
                str(node.attrs.get("src") or "") or None,
            )
        elif node.children:
            _extend_spans(spans, _inline_spans(
                node.children,
                style,
                hyperlink,
            ))
        elif node.content:
            _append_span(spans, node.content, style, hyperlink)

    return spans


def _code_lines(text: str, *, language: str) -> list[list[TextSpan]]:
    """使用 Pygments 渲染代码块。"""
    code = str(text or "").rstrip("\n")
    if not code:
        return [[]]

    lexer         = None
    language_info = str(language or "").strip()
    lexer_name    = language_info.split(maxsplit=1)[0] if language_info else ""

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
                _append_span(
                    lines[-1],
                    chunk,
                    span.style,
                    span.hyperlink,
                )
            if index < len(chunks) - 1:
                lines.append([])

    if lines and not lines[-1]:
        lines.pop()

    return lines or [[]]


def _apply_style(spans: list[TextSpan], style: TextStyle) -> list[TextSpan]:
    """把基础样式合并到一行片段。"""
    return [
        TextSpan(
            span.text,
            _merge_style(span.style, style),
            span.hyperlink,
        )
        for span in spans
    ]


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
        strikethrough=(
            base.strikethrough or overlay.strikethrough
        ),
    )


def _extend_spans(target: list[TextSpan], source: list[TextSpan]) -> None:
    """追加并合并一组相邻样式片段。"""
    for span in source:
        _append_span(target, span.text, span.style, span.hyperlink)


def _append_span(
    spans: list[TextSpan],
    text: str,
    style: TextStyle,
    hyperlink: str | None = None
) -> None:
    """追加片段并合并相邻同样式内容。"""
    if not text:
        return None
    if (
        spans
        and spans[-1].style == style
        and spans[-1].hyperlink == hyperlink
    ):
        previous = spans[-1]
        spans[-1] = TextSpan(f"{previous.text}{text}", style, hyperlink)
        return None
    spans.append(TextSpan(text, style, hyperlink))


if __name__ == '__main__':
    pass
