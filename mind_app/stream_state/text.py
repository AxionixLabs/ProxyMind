# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_app.presentation.models import StyledBlock, TextSpan, TextStyle
from mind_app.stream_state.boundary import (
    ExternalOutputBoundary,
    OutputBoundaryState
)
from mind_app.stream_state.text_models import TextFinalUnit


class TextState(object):
    """管理正文、块文本、可见窗口和输出边界状态。"""

    STREAM          = "stream"
    BLOCK           = "block"
    ELLIPSIS        = " ..."
    MIN_LINE_LIMIT  = 48
    MAX_LINE_LIMIT  = 160
    LINE_PADDING    = 6
    MIN_BLOCK_LIMIT = 96
    MAX_BLOCK_LIMIT = 320
    BLOCK_LINES     = 2

    def __init__(
        self,
        width_provider: typing.Callable[[], int | None] | None = None,
    ) -> None:
        """初始化文本段、可见文本和最终正文缓存。"""
        self.width_provider = width_provider or (lambda: None)
        self.display_segments: list[dict[str, typing.Any]] = []
        self.visible_spans: list[TextSpan] = []

        self.boundary = OutputBoundaryState(stream_display=self.STREAM)

        self.display_text: str = ""
        self.raw_text: str     = ""

        self._external_boundary: ExternalOutputBoundary | None = None

    @property
    def trailing_newlines(self) -> int:
        """返回当前输出边界末尾的连续换行数量。"""
        return self.boundary.trailing_newlines

    @property
    def stream_only(self) -> bool:
        """返回当前可见内容是否全部来自流式正文。"""
        return bool(self.display_segments) and all(
            segment.get("mode") == self.STREAM
            for segment in self.display_segments
        )

    @trailing_newlines.setter
    def trailing_newlines(self, value: int) -> None:
        """设置当前输出边界末尾的连续换行数量。"""
        self.boundary.trailing_newlines = max(0, int(value or 0))

    @property
    def at_line_start(self) -> bool:
        """返回当前输出边界是否位于行首。"""
        return self.boundary.at_line_start

    @at_line_start.setter
    def at_line_start(self, value: bool) -> None:
        """设置当前输出边界是否位于行首。"""
        self.boundary.at_line_start = bool(value)

    @property
    def last_display(self) -> str | None:
        """返回上一段输出的显示模式。"""
        return self.boundary.last_display

    @last_display.setter
    def last_display(self, value: str | None) -> None:
        """设置上一段输出的显示模式。"""
        self.boundary.last_display = value

    @property
    def external_boundary(self) -> dict[str, typing.Any] | None:
        """返回外部直接输出边界的兼容字典表示。"""
        if self._external_boundary is None:
            return None
        return self._external_boundary.as_dict()

    @external_boundary.setter
    def external_boundary(
        self,
        value: dict[str, typing.Any] | ExternalOutputBoundary | None
    ) -> None:
        """设置外部直接输出边界，兼容旧字典结构。"""
        if value is None:
            self._external_boundary = None
        elif isinstance(value, ExternalOutputBoundary):
            self._external_boundary = value
        else:
            self._external_boundary = ExternalOutputBoundary.from_dict(value)

    def append(
        self,
        chunk: typing.Optional[str],
        *,
        display: str = STREAM,
        display_chunk: typing.Optional[str] = None,
        raw_chunk: typing.Optional[str] = None,
        display_style: TextStyle | None = None,
        display_parts: list[TextSpan] | None = None,
        preserve_display_parts: bool = False,
        echo: bool = True
    ) -> bool:
        """追加一段文本并返回是否适合继续增量渲染动画。"""
        if not echo or not chunk:
            return False

        raw_delta = ""
        if display_parts is not None:
            visible_parts = self._normalize_display_parts(display_parts, display=display)
        else:
            visible_delta = str(display_chunk) if display_chunk is not None else str(chunk)
            visible_delta = self._normalize_display_text(visible_delta, display=display)

            visible_parts = [
                TextSpan(visible_delta, display_style or TextStyle())
            ] if visible_delta else []

        if not visible_parts:
            return False

        visible_delta = self._spans_text(visible_parts)
        if display == self.STREAM and display_parts is None and display_style is None and display_chunk is None:
            raw_delta = str(chunk)
        elif display == self.STREAM and raw_chunk is not None:
            raw_delta = str(raw_chunk)

        self._append_segment(
            display,
            visible_delta,
            visible_parts,
            raw_delta=raw_delta,
            preserve_display_parts=preserve_display_parts
        )
        self.raw_text += raw_delta
        self.visible_spans = self._compose_visible_spans()

        visible = self._spans_text(self.visible_spans)
        animate = (display == self.STREAM and visible.startswith(self.display_text))

        self.display_text = visible
        self.boundary.observe_display(display=display, text=visible_delta)

        return animate

    def visible_block(self, text: str | None = None) -> StyledBlock:
        """返回全部或指定尾部窗口对应的中立展示块。"""
        selected = self.display_text if text is None else str(text)
        if selected == self.display_text:
            spans = self.visible_spans or [TextSpan(self.display_text)]
        else:
            start = self.display_text.rfind(selected)
            if start < 0:
                start = max(0, len(self.display_text) - len(selected))
            spans = self._slice_spans(
                self.visible_spans or [TextSpan(self.display_text)],
                start,
                len(self.display_text),
            )
        return StyledBlock(plain_text=selected, spans=tuple(spans))

    def final_units(self) -> tuple[TextFinalUnit, ...]:
        """返回最终落版所需的中立文本单元。"""
        if self._markdown_final_enabled():
            return (TextFinalUnit(
                kind="markdown",
                text=self.raw_text.rstrip("\n"),
            ),)
        return self._mixed_final_units()

    def has_styles(self) -> bool:
        """判断当前可见文本是否包含显式样式。"""
        return any(span.style != TextStyle() for span in self.visible_spans)

    def remember_external_output(self, *, display: str, text: str) -> None:
        """记录动态渲染器之外直接输出的段落边界。"""
        if not text:
            return None

        self.boundary.observe_display(display=display, text=text)

        self._external_boundary = ExternalOutputBoundary.from_output(
            display=display,
            text=text
        )

    def remember_external_spacing(self, *, display: str, text: str) -> None:
        """记录外部 UI 前主动打印的空白边界。"""
        if not text:
            return None
        if self._external_boundary is None:
            self._external_boundary = ExternalOutputBoundary.from_output(
                display=display,
                text=text
            )
            return None

        self._external_boundary = self._external_boundary.with_spacing(text)
        self.boundary.observe_raw(text)

    def segment_prefix_for(
        self,
        *,
        display: str,
        incoming_text: str | None = None
    ) -> str:
        """返回下一段输出应补的段间前缀，不修改状态。"""
        return self._segment_prefix(for_display=display, incoming_text=incoming_text)

    def clear(self) -> None:
        """清空所有文本状态和缓存。"""
        self.display_segments.clear()
        self.visible_spans.clear()

        self.display_text      = ""
        self.raw_text          = ""
        self.boundary.clear()
        self._external_boundary = None

    def _markdown_final_enabled(self) -> bool:
        """判断最终落版是否可以使用 Markdown 渲染。"""
        if not self.raw_text.strip():
            return False
        if self._has_external_boundary():
            return False
        if not self.display_segments:
            return False
        for segment in self.display_segments:
            if segment.get("mode") != self.STREAM:
                return False
            for span in segment.get("spans") or []:
                if span.style != TextStyle():
                    return False
        return True

    def _mixed_final_units(self) -> tuple[TextFinalUnit, ...]:
        """按 segment 类型生成最终落版单元。"""
        units: list[TextFinalUnit] = []
        pending_markdown_visible: list[str] = []
        pending_markdown_raw: list[str]     = []

        pending_spans: list[TextSpan] = []

        def flush_markdown() -> None:
            """把待合并的 Markdown 文本写入最终落版单元。"""
            markdown_visible = "".join(pending_markdown_visible)
            markdown_raw = "".join(pending_markdown_raw).rstrip("\n")
            pending_markdown_visible.clear()
            pending_markdown_raw.clear()
            if markdown_visible.strip() and markdown_raw.strip():
                units.append(TextFinalUnit(
                    kind="markdown",
                    text=markdown_raw.lstrip("\n"),
                    gap_before=self._should_gap_before_final_unit(
                        markdown_visible,
                        has_previous=bool(units),
                    ),
                ))

        def flush_spans() -> None:
            """把待合并的样式片段写入最终落版单元。"""
            if not pending_spans:
                return None
            spans_visible = self._spans_text(pending_spans)
            spans = tuple(self._strip_spans_outer_newlines(pending_spans))
            pending_spans.clear()

            if spans_visible.strip():
                units.append(TextFinalUnit(
                    kind="spans",
                    text=spans_visible.rstrip("\n"),
                    spans=spans,
                    gap_before=self._should_gap_before_final_unit(
                        spans_visible,
                        has_previous=bool(units),
                    ),
                ))

        for segment in self.display_segments:
            if self._segment_markdown_enabled(segment):
                flush_spans()
                visible = str(segment.get("text") or "")
                raw = str(segment.get("raw_text") or visible.lstrip("\n"))
                pending_markdown_visible.append(visible)
                pending_markdown_raw.append(raw)
                continue

            flush_markdown()
            segment_spans = segment.get("spans") or []
            self._extend_spans(pending_spans, segment_spans)

        flush_markdown()
        flush_spans()

        if units:
            return tuple(units)
        block = self.visible_block()
        return (TextFinalUnit(
            kind="spans",
            text=block.plain_text.rstrip("\n"),
            spans=tuple(self._strip_spans_outer_newlines(list(block.spans))),
        ),)

    def _append_segment(
        self,
        display: str,
        delta: str,
        spans: list[TextSpan],
        *,
        raw_delta: str = "",
        preserve_display_parts: bool = False
    ) -> None:
        """追加一个显示段，并合并连续 stream 段。"""
        if (
            display == self.STREAM
            and self.display_segments
            and self.display_segments[-1]["mode"] == self.STREAM
        ):
            self.display_segments[-1]["text"] += delta
            self.display_segments[-1]["spans"].extend(spans)
            self.display_segments[-1]["raw_text"] += raw_delta
            self.display_segments[-1]["preserve_display_parts"] = bool(
                self.display_segments[-1].get("preserve_display_parts")
            ) and preserve_display_parts
            return None

        self.display_segments.append({
            "mode"                   : display,
            "text"                   : delta,
            "spans"                  : spans,
            "raw_text"               : raw_delta,
            "preserve_display_parts" : preserve_display_parts
        })

    def _compose_visible_spans(self) -> list[TextSpan]:
        """根据所有显示段生成裁剪后的可见片段。"""
        styled_spans: list[TextSpan] = []

        line_limit  = self._line_limit()
        block_limit = self._block_limit(line_limit)

        for segment in self.display_segments:
            mode = segment["mode"]
            segment_spans = segment.get("spans") or [
                TextSpan(str(segment.get("text") or ""))
            ]
            if mode == self.BLOCK:
                if segment.get("preserve_display_parts"):
                    self._extend_spans(styled_spans, segment_spans)
                    continue
                self._extend_spans(
                    styled_spans,
                    self._render_block_spans(segment_spans, block_limit)
                )
                continue
            self._extend_spans(
                styled_spans,
                self._render_stream_spans(segment_spans, line_limit)
            )

        return styled_spans

    def _line_limit(self) -> int:
        """根据终端宽度计算流式行宽限制。"""
        width = max(0, int(self.width_provider() or 0))
        limit = width - self.LINE_PADDING
        return max(self.MIN_LINE_LIMIT, min(self.MAX_LINE_LIMIT, limit))

    def _block_limit(self, line_limit: int) -> int:
        """根据行宽计算块文本总字符限制。"""
        limit = line_limit * self.BLOCK_LINES
        return max(self.MIN_BLOCK_LIMIT, min(self.MAX_BLOCK_LIMIT, limit))

    def _normalize_display_text(self, text: str, *, display: str) -> str:
        """按显示模式归一化纯文本输入。"""
        if display == self.BLOCK:
            return self._normalize_block_text(text)
        return self._normalize_stream_text(text)

    def _normalize_display_parts(
        self,
        parts: list[TextSpan],
        *,
        display: str
    ) -> list[TextSpan]:
        """按显示模式归一化带样式的文本片段。"""
        clean = [
            TextSpan(str(part.text or ""), part.style, part.hyperlink)
            for part in parts
            if str(part.text or "")
        ]

        raw_text = self._spans_text(clean)
        if not raw_text:
            return []

        if display == self.BLOCK:
            body = raw_text.strip("\n")
            if not body:
                return []
            trailing = min(2, OutputBoundaryState.count_trailing_newlines(raw_text))
            if trailing <= 0:
                trailing = 1
            prefix = self._segment_prefix(for_display=self.BLOCK)
            out: list[TextSpan] = []
            if prefix:
                out.append(TextSpan(prefix))
            self._extend_spans(
                out,
                self._slice_spans(
                    clean,
                    raw_text.find(body),
                    raw_text.find(body) + len(body),
                ),
            )
            out.append(TextSpan("\n" * trailing))
            return out

        prefix = self._segment_prefix(for_display=self.STREAM, incoming_text=raw_text)
        out = []
        if prefix:
            out.append(TextSpan(prefix))
        self._extend_spans(out, clean)
        return out

    def _normalize_block_text(self, text: str) -> str:
        """归一化块文本的前后换行。"""
        body = text.strip("\n")
        if not body:
            return ""

        prefix = self._segment_prefix(for_display=self.BLOCK)
        return f"{prefix}{body}\n"

    def _normalize_stream_text(self, text: str) -> str:
        """归一化流式文本的段间前缀。"""
        if not text:
            return ""

        prefix = self._segment_prefix(for_display=self.STREAM, incoming_text=text)
        return f"{prefix}{text}"

    def _segment_prefix(
        self,
        *,
        for_display: str,
        incoming_text: str | None = None
    ) -> str:
        """根据上一段输出状态生成段间换行。"""
        return self.boundary.prefix(for_display=for_display, incoming_text=incoming_text)

    @classmethod
    def _segment_markdown_enabled(
        cls,
        segment: dict[str, typing.Any]
    ) -> bool:
        """判断指定片段是否可按 Markdown 最终渲染。"""
        if segment.get("mode") != cls.STREAM:
            return False
        if not str(segment.get("raw_text") or segment.get("text") or "").strip():
            return False
        for span in segment.get("spans") or []:
            if span.style != TextStyle():
                return False

        return True

    @classmethod
    def _render_block_spans(
        cls,
        spans: list[TextSpan],
        limit: int
    ) -> list[TextSpan]:
        """按块文本限制裁剪带样式片段。"""
        rendered = cls._take_visible_chars(spans, limit + 1)
        if cls._visible_len(rendered) <= limit:
            return rendered

        keep = max(0, limit - len(cls.ELLIPSIS))
        out  = cls._take_visible_chars(spans, keep)

        while out and out[-1].text.endswith("\n"):
            trimmed = out[-1].text.rstrip("\n")
            if trimmed:
                out[-1] = TextSpan(trimmed, out[-1].style)
            else:
                out.pop()

        cls._append_span(out, cls.ELLIPSIS, TextStyle())
        if cls._spans_text(spans).endswith("\n"):
            cls._append_span(out, "\n", TextStyle())

        return out

    @classmethod
    def _render_stream_spans(
        cls,
        spans: list[TextSpan],
        limit: int
    ) -> list[TextSpan]:
        """按行宽限制裁剪流式带样式片段。"""
        out: list[TextSpan] = []
        line_spans: list[TextSpan] = []

        line_len: int  = 0
        line_cut: bool = False

        for span in spans:
            for ch in span.text:
                if ch == "\n":
                    cls._extend_spans(out, line_spans)
                    line_spans = []
                    cls._append_span(
                        out,
                        "\n",
                        span.style,
                        span.hyperlink,
                    )
                    line_len = 0
                    line_cut = False
                    continue
                if line_cut:
                    continue
                if line_len < limit:
                    cls._append_span(
                        line_spans,
                        ch,
                        span.style,
                        span.hyperlink,
                    )
                    line_len += 1
                    continue
                keep = max(0, limit - len(cls.ELLIPSIS))
                cls._extend_spans(out, cls._take_visible_chars(line_spans, keep))
                cls._append_span(out, cls.ELLIPSIS, TextStyle())
                line_spans = []
                line_cut = True

        cls._extend_spans(out, line_spans)
        return out

    @classmethod
    def _take_visible_chars(
        cls,
        spans: list[TextSpan],
        limit: int
    ) -> list[TextSpan]:
        """从片段列表中按可见字符数截取前缀。"""
        out: list[TextSpan] = []

        visible: int = 0

        for span in spans:
            for ch in span.text:
                if ch != "\n":
                    if visible >= limit:
                        return out
                    visible += 1
                cls._append_span(out, ch, span.style, span.hyperlink)

        return out

    @classmethod
    def _slice_spans(
        cls,
        spans: list[TextSpan],
        start: int,
        end: int
    ) -> list[TextSpan]:
        """按字符串位置切取片段列表。"""
        out: list[TextSpan] = []

        pos: int = 0
        for span in spans:
            text = span.text
            next_pos = pos + len(text)
            if next_pos <= start:
                pos = next_pos
                continue
            if pos >= end:
                break
            chunk = text[max(0, start - pos):max(0, end - pos)]
            if chunk:
                cls._append_span(out, chunk, span.style, span.hyperlink)
            pos = next_pos

        return out

    @classmethod
    def _extend_spans(
        cls,
        target: list[TextSpan],
        source: list[TextSpan]
    ) -> None:
        """把源片段追加到目标片段列表。"""
        for span in source:
            cls._append_span(
                target,
                span.text,
                span.style,
                span.hyperlink,
            )

    def _should_gap_before_final_unit(
        self,
        visible: str,
        *,
        has_previous: bool
    ) -> bool:
        """读取 segment 边界，转换为最终落版的显式段间空行。"""
        if has_previous:
            return self._count_leading_newlines(visible) > 0
        if not self._has_external_boundary():
            return False
        if self._external_boundary_trailing_newlines() >= 2:
            return False

        return bool(str(visible or "").strip())

    def _has_external_boundary(self) -> bool:
        """判断当前最终落版前是否存在直接输出边界。"""
        return bool(self._external_boundary and self._external_boundary.has_text)

    def _external_boundary_trailing_newlines(self) -> int:
        """返回直接输出边界尾部换行数。"""
        if self._external_boundary is None:
            return 0
        return self._external_boundary.trailing_newlines

    @classmethod
    def _strip_spans_leading_newlines(
        cls,
        spans: list[TextSpan]
    ) -> list[TextSpan]:
        """移除片段列表开头的连续换行。"""
        out = list(spans)

        while out and out[0].text.startswith("\n"):
            text = out[0].text.lstrip("\n")
            if text:
                out[0] = TextSpan(text, out[0].style)
                break
            out.pop(0)

        return out

    @classmethod
    def _strip_spans_outer_newlines(
        cls,
        spans: list[TextSpan],
    ) -> list[TextSpan]:
        """移除片段列表两端由边界状态接管的连续换行。"""
        out = cls._strip_spans_leading_newlines(spans)
        while out and out[-1].text.endswith("\n"):
            text = out[-1].text.rstrip("\n")
            if text:
                out[-1] = TextSpan(text, out[-1].style)
                break
            out.pop()
        return out

    @staticmethod
    def _visible_len(spans: list[TextSpan]) -> int:
        """统计片段中的非换行字符数量。"""
        return sum(1 for ch in TextState._spans_text(spans) if ch != "\n")

    @staticmethod
    def _spans_text(spans: list[TextSpan]) -> str:
        """把片段列表合并为纯文本。"""
        return "".join(span.text for span in spans)

    @staticmethod
    def _count_leading_newlines(text: str) -> int:
        """统计文本开头连续换行数量。"""
        count = 0
        for ch in str(text or ""):
            if ch != "\n":
                break
            count += 1
        return count

    @staticmethod
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

            spans[-1] = TextSpan(
                f"{previous.text}{text}",
                style,
                hyperlink,
            )
            return None
        spans.append(TextSpan(text, style, hyperlink))


if __name__ == '__main__':
    pass
