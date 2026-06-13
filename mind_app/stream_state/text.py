# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from rich.console import Group
from rich.text import Text
from mind_core.design import Design
from mind_app.stream_state.markdown import render_markdown


class TextState(object):
    """管理正文/块文本的可见内容和裁剪规则。"""

    STREAM          = "stream"
    BLOCK           = "block"
    ELLIPSIS        = " ..."
    MIN_LINE_LIMIT  = 48
    MAX_LINE_LIMIT  = 160
    LINE_PADDING    = 6
    MIN_BLOCK_LIMIT = 96
    MAX_BLOCK_LIMIT = 320
    BLOCK_LINES     = 2

    def __init__(self) -> None:
        """初始化文本段、可见文本和最终正文缓存。"""
        self.display_segments: list[dict[str, typing.Any]]           = []
        self.visible_segments: list[dict[str, typing.Optional[str]]] = []

        self.trailing_newlines: int = 0
        self.display_text: str      = ""
        self.raw_text: str          = ""

        self.at_line_start: bool      = True
        self.last_display: str | None = None

        self.external_boundary: dict[str, typing.Any] | None = None

    def append(
        self,
        chunk: typing.Optional[str],
        *,
        display: str = STREAM,
        display_chunk: typing.Optional[str] = None,
        raw_chunk: typing.Optional[str] = None,
        display_style: typing.Optional[str] = None,
        display_parts: typing.Optional[list[dict[str, typing.Optional[str]]]] = None,
        echo: bool = True
    ) -> bool:
        """追加一段文本并返回是否适合继续打字机动画。"""
        if not echo or not chunk:
            return False

        raw_delta = ""
        if display_parts is not None:
            visible_parts = self._normalize_display_parts(display_parts, display=display)
        else:
            visible_delta = str(display_chunk) if display_chunk is not None else str(chunk)
            visible_delta = self._normalize_display_text(visible_delta, display=display)

            visible_parts = [
                {"text": visible_delta, "style": display_style}
            ] if visible_delta else []

        if not visible_parts:
            return False

        visible_delta = self._parts_text(visible_parts)
        if display == self.STREAM and display_parts is None and display_style is None and display_chunk is None:
            raw_delta = str(chunk)
        elif display == self.STREAM and raw_chunk is not None:
            raw_delta = str(raw_chunk)

        self._append_segment(display, visible_delta, visible_parts, raw_delta=raw_delta)
        self.raw_text += raw_delta
        self.visible_segments = self._compose_visible_segments()

        visible = self._parts_text(self.visible_segments)
        animate = (display == self.STREAM and visible.startswith(self.display_text))

        self.display_text      = visible
        self.at_line_start     = visible_delta.endswith("\n")
        self.trailing_newlines = self._count_trailing_newlines(visible_delta)
        self.last_display      = display

        return animate

    def renderable(self) -> Text:
        """返回当前可见文本的 Rich Text。"""
        return self.renderable_for_text(self.display_text)

    def final_renderable(self) -> typing.Any:
        """返回最终落版 renderable；纯正文用 Markdown，结构化内容保留 Rich Text。"""
        if self._markdown_final_enabled():
            return render_markdown(self.raw_text.rstrip("\n"))
        return self._mixed_final_renderable()

    def renderable_for_text(self, text: str) -> Text:
        """按给定文本窗口生成对应的 Rich Text。"""
        if text == self.display_text:
            parts = self.visible_segments or [{"text": self.display_text, "style": None}]
        else:
            start = self.display_text.rfind(text)
            if start < 0:
                start = max(0, len(self.display_text) - len(text))
            parts = self._slice_parts(
                self.visible_segments or [{"text": self.display_text, "style": None}],
                start,
                len(self.display_text)
            )

        out = Text()

        for part in parts:
            text = str(part.get("text") or "")
            if not text:
                continue
            out.append(text, style=str(part.get("style") or "bold"))

        return out

    def has_styles(self) -> bool:
        """判断当前可见文本是否包含显式样式。"""
        return any(bool(part.get("style")) for part in self.visible_segments)

    def status_spacer(self) -> str:
        """返回正文和状态行之间需要补充的换行。"""
        if not self.display_text:
            return ""
        if self.display_text.endswith("\n"):
            return ""

        return "\n"

    def remember_external_output(self, *, display: str, text: str) -> None:
        """记录非 live renderer 直接输出的段落边界。"""
        if not text:
            return None

        self.at_line_start     = text.endswith("\n")
        self.trailing_newlines = self._count_trailing_newlines(text)
        self.last_display      = display

        self.external_boundary = {
            "display"           : display,
            "trailing_newlines" : self.trailing_newlines,
            "has_text"          : bool(str(text or "").strip())
        }

    def clear(self) -> None:
        """清空所有文本状态和缓存。"""
        self.display_segments.clear()
        self.visible_segments.clear()

        self.display_text      = ""
        self.raw_text          = ""
        self.at_line_start     = True
        self.trailing_newlines = 0
        self.last_display      = None
        self.external_boundary = None

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
            for part in segment.get("parts") or []:
                if part.get("style"):
                    return False
        return True

    def _mixed_final_renderable(self) -> typing.Any:
        """按 segment 类型分别生成最终落版，正文段保留 Markdown。"""
        units: list[dict[str, typing.Any]]  = []
        pending_markdown_visible: list[str] = []
        pending_markdown_raw: list[str]     = []

        pending_parts: list[dict[str, typing.Optional[str]]] = []

        def flush_markdown() -> None:
            markdown_visible = "".join(pending_markdown_visible)
            markdown_raw = "".join(pending_markdown_raw).rstrip("\n")
            pending_markdown_visible.clear()
            pending_markdown_raw.clear()
            if markdown_visible.strip() and markdown_raw.strip():
                units.append({
                    "kind"    : "markdown",
                    "visible" : markdown_visible,
                    "text"    : markdown_raw,
                    "gap"     : self._should_gap_before_final_unit(markdown_visible, has_previous=bool(units))
                })

        def flush_parts() -> None:
            if not pending_parts:
                return None
            parts = [dict(part) for part in pending_parts]
            pending_parts.clear()
            parts_visible = self._parts_text(parts)

            if parts_visible.strip():
                units.append({
                    "kind"    : "parts",
                    "visible" : parts_visible,
                    "parts"   : parts,
                    "gap"     : self._should_gap_before_final_unit(parts_visible, has_previous=bool(units))
                })

        for segment in self.display_segments:
            if self._segment_markdown_enabled(segment):
                flush_parts()
                visible = str(segment.get("text") or "")
                raw = str(segment.get("raw_text") or visible.lstrip("\n"))
                pending_markdown_visible.append(visible)
                pending_markdown_raw.append(raw)
                continue

            flush_markdown()
            segment_parts = segment.get("parts") or []
            self._extend_parts(pending_parts, segment_parts)

        flush_markdown()
        flush_parts()

        renderables = self._final_renderables_from_units(units)

        if not renderables:
            return self.renderable()
        if len(renderables) == 1:
            return renderables[0]
        return Group(*renderables)

    def _append_segment(
        self,
        display: str,
        delta: str,
        parts: list[dict[str, typing.Optional[str]]],
        *,
        raw_delta: str = ""
    ) -> None:
        """追加一个显示段，并合并连续 stream 段。"""
        if (
            display == self.STREAM
            and self.display_segments
            and self.display_segments[-1]["mode"] == self.STREAM
        ):
            self.display_segments[-1]["text"] += delta
            self.display_segments[-1]["parts"].extend(parts)
            self.display_segments[-1]["raw_text"] += raw_delta
            return None

        self.display_segments.append({
            "mode"     : display,
            "text"     : delta,
            "parts"    : parts,
            "raw_text" : raw_delta
        })

    def _compose_visible_text(self) -> str:
        """组合当前可见文本。"""
        return self._parts_text(self._compose_visible_segments())

    def _compose_visible_segments(self) -> list[dict[str, typing.Optional[str]]]:
        """根据所有显示段生成裁剪后的可见片段。"""
        styled_parts: list[dict[str, typing.Optional[str]]] = []

        line_limit  = self._line_limit()
        block_limit = self._block_limit(line_limit)

        for segment in self.display_segments:
            mode = segment["mode"]
            segment_parts = segment.get("parts") or [
                {"text": str(segment.get("text") or ""), "style": None}
            ]
            if mode == self.BLOCK:
                self._extend_parts(
                    styled_parts,
                    self._render_block_parts(segment_parts, block_limit)
                )
                continue
            self._extend_parts(
                styled_parts,
                self._render_stream_parts(segment_parts, line_limit)
            )

        return styled_parts

    def _render_block(self, delta: str, limit: int) -> str:
        """按块文本限制裁剪单段纯文本。"""
        parts: list[str] = []
        visible = 0
        trimmed = False

        for ch in delta:
            if ch == "\n":
                parts.append(ch)
                continue
            if visible < limit:
                parts.append(ch)
                visible += 1
                continue
            trimmed = True
            break

        out = "".join(parts)
        if trimmed:
            self._trim_visible_tail(parts, limit, len(self.ELLIPSIS))
            out = "".join(parts).rstrip("\n")
            if not out.endswith(self.ELLIPSIS):
                out = f"{out}{self.ELLIPSIS}"
            if delta.endswith("\n") and not out.endswith("\n"):
                out += "\n"
        return out

    def _render_stream(self, delta: str, limit: int) -> str:
        """按行宽限制裁剪流式纯文本。"""
        parts: list[str] = []

        line_start = 0
        line_len   = 0
        line_cut   = False

        for ch in delta:
            if ch == "\n":
                parts.append("\n")
                line_start = len(parts)
                line_len = 0
                line_cut = False
                continue

            if line_cut:
                continue

            if line_len < limit:
                parts.append(ch)
                line_len += 1
                continue

            need = max(0, line_len - (limit - len(self.ELLIPSIS)))

            removed = self._trim_tail(parts, line_start, need)
            if removed == need:
                parts.append(self.ELLIPSIS)
            line_cut = True

        return "".join(parts)

    def _line_limit(self) -> int:
        """根据终端宽度计算流式行宽限制。"""
        width = max(0, int(getattr(Design.console, "width", 0) or 0))
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
        parts: list[dict[str, typing.Optional[str]]],
        *,
        display: str
    ) -> list[dict[str, typing.Optional[str]]]:
        """按显示模式归一化带样式的文本片段。"""
        clean = [
            {"text": str(part.get("text") or ""), "style": part.get("style")}
            for part in parts
            if str(part.get("text") or "")
        ]
        raw_text = self._parts_text(clean)
        if not raw_text:
            return []

        if display == self.BLOCK:
            body = raw_text.strip("\n")
            if not body:
                return []
            trailing = min(2, self._count_trailing_newlines(raw_text))
            if trailing <= 0:
                trailing = 1
            prefix = self._segment_prefix(for_display=self.BLOCK)
            out: list[dict[str, typing.Optional[str]]] = []
            if prefix:
                out.append({"text": prefix, "style": None})
            self._extend_parts(
                out, self._slice_parts(clean, raw_text.find(body), raw_text.find(body) + len(body))
            )
            out.append({"text": "\n" * trailing, "style": None})
            return out

        prefix = self._segment_prefix(for_display=self.STREAM, incoming_text=raw_text)
        out = []
        if prefix:
            out.append({"text": prefix, "style": None})
        self._extend_parts(out, clean)
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
        if self.last_display is None:
            return ""

        if self.last_display == self.STREAM and for_display == self.STREAM:
            return ""

        if self.trailing_newlines >= 2:
            return ""

        if for_display == self.STREAM and incoming_text and incoming_text.startswith("\n"):
            return ""

        return "\n" * (2 - self.trailing_newlines)

    @classmethod
    def _segment_markdown_enabled(
        cls,
        segment: dict[str, typing.Any]
    ) -> bool:
        if segment.get("mode") != cls.STREAM:
            return False
        if not str(segment.get("raw_text") or segment.get("text") or "").strip():
            return False
        for part in segment.get("parts") or []:
            if part.get("style"):
                return False

        return True

    @classmethod
    def _render_block_parts(
        cls,
        parts: list[dict[str, typing.Optional[str]]],
        limit: int
    ) -> list[dict[str, typing.Optional[str]]]:
        """按块文本限制裁剪带样式片段。"""
        rendered = cls._take_visible_chars(parts, limit + 1)
        if cls._visible_len(rendered) <= limit:
            return rendered

        keep = max(0, limit - len(cls.ELLIPSIS))
        out  = cls._take_visible_chars(parts, keep)

        while out and str(out[-1].get("text") or "").endswith("\n"):
            out[-1]["text"] = str(out[-1].get("text") or "").rstrip("\n")
            if not out[-1]["text"]:
                out.pop()

        cls._append_part(out, cls.ELLIPSIS, None)
        if cls._parts_text(parts).endswith("\n"):
            cls._append_part(out, "\n", None)

        return out

    @classmethod
    def _render_stream_parts(
        cls,
        parts: list[dict[str, typing.Optional[str]]],
        limit: int
    ) -> list[dict[str, typing.Optional[str]]]:
        """按行宽限制裁剪流式带样式片段。"""
        out: list[dict[str, typing.Optional[str]]]        = []
        line_parts: list[dict[str, typing.Optional[str]]] = []

        line_len: int  = 0
        line_cut: bool = False

        for part in parts:
            style = part.get("style")
            for ch in str(part.get("text") or ""):
                if ch == "\n":
                    cls._extend_parts(out, line_parts)
                    line_parts = []
                    cls._append_part(out, "\n", style)
                    line_len = 0
                    line_cut = False
                    continue
                if line_cut:
                    continue
                if line_len < limit:
                    cls._append_part(line_parts, ch, style)
                    line_len += 1
                    continue
                keep = max(0, limit - len(cls.ELLIPSIS))
                cls._extend_parts(out, cls._take_visible_chars(line_parts, keep))
                cls._append_part(out, cls.ELLIPSIS, None)
                line_parts = []
                line_cut = True

        cls._extend_parts(out, line_parts)
        return out

    @classmethod
    def _take_visible_chars(
        cls,
        parts: list[dict[str, typing.Optional[str]]],
        limit: int
    ) -> list[dict[str, typing.Optional[str]]]:
        """从片段列表中按可见字符数截取前缀。"""
        out: list[dict[str, typing.Optional[str]]] = []

        visible: int = 0

        for part in parts:
            style = part.get("style")
            for ch in str(part.get("text") or ""):
                if ch != "\n":
                    if visible >= limit:
                        return out
                    visible += 1
                cls._append_part(out, ch, style)

        return out

    @classmethod
    def _slice_parts(
        cls,
        parts: list[dict[str, typing.Optional[str]]],
        start: int,
        end: int
    ) -> list[dict[str, typing.Optional[str]]]:
        """按字符串位置切取片段列表。"""
        out: list[dict[str, typing.Optional[str]]] = []

        pos: int = 0
        for part in parts:
            text = str(part.get("text") or "")
            next_pos = pos + len(text)
            if next_pos <= start:
                pos = next_pos
                continue
            if pos >= end:
                break
            chunk = text[max(0, start - pos):max(0, end - pos)]
            if chunk:
                cls._append_part(out, chunk, part.get("style"))
            pos = next_pos

        return out

    @classmethod
    def _extend_parts(
        cls,
        target: list[dict[str, typing.Optional[str]]],
        source: list[dict[str, typing.Optional[str]]]
    ) -> None:
        """把源片段追加到目标片段列表。"""
        for part in source:
            cls._append_part(target, str(part.get("text") or ""), part.get("style"))

    def _final_renderables_from_units(
        self,
        units: list[dict[str, typing.Any]]
    ) -> list[typing.Any]:
        """按可见段落边界生成最终落版 renderable。"""
        renderables: list[typing.Any] = []

        for unit in units:
            if unit.get("gap"):
                renderables.append(Text(""))
            renderable = self._unit_renderable(unit)
            if renderable is not None:
                renderables.append(renderable)

        return renderables

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
        return bool(self.external_boundary and self.external_boundary.get("has_text"))

    def _external_boundary_trailing_newlines(self) -> int:
        """返回直接输出边界尾部换行数。"""
        if not self.external_boundary:
            return 0
        return max(0, int(self.external_boundary.get("trailing_newlines") or 0))

    @classmethod
    def _unit_renderable(
        cls,
        unit: dict[str, typing.Any]
    ) -> typing.Any:
        kind = str(unit.get("kind") or "")
        if kind == "markdown":
            text = str(unit.get("text") or "").lstrip("\n").rstrip("\n")
            return render_markdown(text)

        parts = [
            dict(part) for part in unit.get("parts") or []
            if isinstance(part, dict)
        ]
        return cls._parts_renderable(cls._strip_parts_leading_newlines(parts))

    @classmethod
    def _strip_parts_leading_newlines(
        cls,
        parts: list[dict[str, typing.Optional[str]]]
    ) -> list[dict[str, typing.Optional[str]]]:
        out = [dict(part) for part in parts]

        while out and str(out[0].get("text") or "").startswith("\n"):
            text = str(out[0].get("text") or "").lstrip("\n")
            if text:
                out[0]["text"] = text
                break
            out.pop(0)

        return out

    @staticmethod
    def _count_trailing_newlines(text: str) -> int:
        """统计文本尾部连续换行数量。"""
        count = 0
        for ch in reversed(text):
            if ch != "\n":
                break
            count += 1
        return count

    @staticmethod
    def _visible_len(parts: list[dict[str, typing.Optional[str]]]) -> int:
        """统计片段中的非换行字符数量。"""
        return sum(1 for ch in TextState._parts_text(parts) if ch != "\n")

    @staticmethod
    def _parts_text(parts: list[dict[str, typing.Optional[str]]]) -> str:
        """把片段列表合并为纯文本。"""
        return "".join(str(part.get("text") or "") for part in parts)

    @staticmethod
    def _count_leading_newlines(text: str) -> int:
        count = 0
        for ch in str(text or ""):
            if ch != "\n":
                break
            count += 1
        return count

    @staticmethod
    def _parts_renderable(parts: list[dict[str, typing.Optional[str]]]) -> Text:
        out = Text()
        for part in parts:
            text = str(part.get("text") or "")
            if not text:
                continue
            out.append(text, style=str(part.get("style") or "bold"))
        out.rstrip()
        return out

    @staticmethod
    def _append_part(
        parts: list[dict[str, typing.Optional[str]]],
        text: str,
        style: typing.Optional[str]
    ) -> None:
        """追加片段并合并相邻同样式内容。"""
        if not text:
            return None
        if parts and parts[-1].get("style") == style:
            parts[-1]["text"] = str(parts[-1].get("text") or "") + text
            return None
        parts.append({"text": text, "style": style})

    @staticmethod
    def _trim_tail(
        parts: list[str],
        line_start: int,
        count: int
    ) -> int:
        """从行尾移除指定数量的字符。"""
        removed = 0
        while count > 0 and len(parts) > line_start:
            parts.pop()
            count -= 1
            removed += 1
        return removed

    @staticmethod
    def _trim_visible_tail(
        parts: list[str],
        limit: int,
        reserve: int
    ) -> None:
        """保留指定可见字符数并为省略标记预留空间。"""
        keep    = max(0, limit - reserve)
        visible = 0

        kept: list[str] = []

        for ch in parts:
            if ch == "\n":
                kept.append(ch)
                continue
            if visible >= keep:
                continue
            kept.append(ch)
            visible += 1

        parts[:] = kept


if __name__ == '__main__':
    pass
