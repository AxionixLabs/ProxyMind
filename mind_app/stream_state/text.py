# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from rich.text import Text
from mind_core.design import Design


TextPart = dict[str, typing.Optional[str]]


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
        self.display_segments: list[dict[str, typing.Any]] = []
        self.visible_segments: list[TextPart] = []
        self.display_text: str = ""
        self.at_line_start: bool = True
        self.trailing_newlines: int = 0
        self.last_display: str | None = None

    def append(
        self,
        chunk: typing.Optional[str],
        *,
        display: str = STREAM,
        display_chunk: typing.Optional[str] = None,
        display_style: typing.Optional[str] = None,
        display_parts: typing.Optional[list[TextPart]] = None,
        echo: bool = True
    ) -> bool:
        if not echo or not chunk:
            return False

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
        self._append_segment(display, visible_delta, visible_parts)
        self.visible_segments = self._compose_visible_segments()
        visible = self._parts_text(self.visible_segments)
        animate = (display == self.STREAM and visible.startswith(self.display_text))
        self.display_text = visible
        self.at_line_start = visible_delta.endswith("\n")
        self.trailing_newlines = self._count_trailing_newlines(visible_delta)
        self.last_display = display
        return animate

    def renderable(self) -> Text:
        return self.renderable_for_text(self.display_text)

    def renderable_for_text(self, text: str) -> Text:
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
        return any(bool(part.get("style")) for part in self.visible_segments)

    def status_spacer(self) -> str:
        if not self.display_text:
            return ""
        if self.display_text.endswith("\n"):
            return ""
        return "\n"

    def _append_segment(self, display: str, delta: str, parts: list[TextPart]) -> None:
        if (
            display == self.STREAM
            and self.display_segments
            and self.display_segments[-1]["mode"] == self.STREAM
        ):
            self.display_segments[-1]["text"] += delta
            self.display_segments[-1]["parts"].extend(parts)
            return None

        self.display_segments.append({"mode": display, "text": delta, "parts": parts})

    def _compose_visible_text(self) -> str:
        return self._parts_text(self._compose_visible_segments())

    def _compose_visible_segments(self) -> list[TextPart]:
        parts: list[str] = []
        styled_parts: list[TextPart] = []
        line_limit = self._line_limit()
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
        parts: list[str] = []
        line_start = 0
        line_len = 0
        line_cut = False

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
        width = max(0, int(getattr(Design.console, "width", 0) or 0))
        limit = width - self.LINE_PADDING
        return max(self.MIN_LINE_LIMIT, min(self.MAX_LINE_LIMIT, limit))

    def _block_limit(self, line_limit: int) -> int:
        limit = line_limit * self.BLOCK_LINES
        return max(self.MIN_BLOCK_LIMIT, min(self.MAX_BLOCK_LIMIT, limit))

    def _normalize_display_text(self, text: str, *, display: str) -> str:
        if display == self.BLOCK:
            return self._normalize_block_text(text)
        return self._normalize_stream_text(text)

    def _normalize_display_parts(self, parts: list[TextPart], *, display: str) -> list[TextPart]:
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
            prefix = self._segment_prefix(for_display=self.BLOCK)
            out: list[TextPart] = []
            if prefix:
                out.append({"text": prefix, "style": None})
            self._extend_parts(out, self._slice_parts(clean, raw_text.find(body), raw_text.find(body) + len(body)))
            out.append({"text": "\n", "style": None})
            return out

        prefix = self._segment_prefix(for_display=self.STREAM, incoming_text=raw_text)
        out = []
        if prefix:
            out.append({"text": prefix, "style": None})
        self._extend_parts(out, clean)
        return out

    def _normalize_block_text(self, text: str) -> str:
        body = text.strip("\n")
        if not body:
            return ""

        prefix = self._segment_prefix(for_display=self.BLOCK)
        return f"{prefix}{body}\n"

    def _normalize_stream_text(self, text: str) -> str:
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
        if self.last_display is None:
            return ""

        if self.last_display == self.STREAM and for_display == self.STREAM:
            return ""

        if self.trailing_newlines >= 2:
            return ""

        if for_display == self.STREAM and incoming_text and incoming_text.startswith("\n"):
            return ""

        return "\n" * (2 - self.trailing_newlines)

    @staticmethod
    def _count_trailing_newlines(text: str) -> int:
        count = 0
        for ch in reversed(text):
            if ch != "\n":
                break
            count += 1
        return count

    @classmethod
    def _render_block_parts(cls, parts: list[TextPart], limit: int) -> list[TextPart]:
        rendered = cls._take_visible_chars(parts, limit + 1)
        if cls._visible_len(rendered) <= limit:
            return rendered
        keep = max(0, limit - len(cls.ELLIPSIS))
        out = cls._take_visible_chars(parts, keep)
        while out and str(out[-1].get("text") or "").endswith("\n"):
            out[-1]["text"] = str(out[-1].get("text") or "").rstrip("\n")
            if not out[-1]["text"]:
                out.pop()
        cls._append_part(out, cls.ELLIPSIS, None)
        if cls._parts_text(parts).endswith("\n"):
            cls._append_part(out, "\n", None)
        return out

    @classmethod
    def _render_stream_parts(cls, parts: list[TextPart], limit: int) -> list[TextPart]:
        out: list[TextPart] = []
        line_parts: list[TextPart] = []
        line_len = 0
        line_cut = False

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
    def _take_visible_chars(cls, parts: list[TextPart], limit: int) -> list[TextPart]:
        out: list[TextPart] = []
        visible = 0
        for part in parts:
            style = part.get("style")
            for ch in str(part.get("text") or ""):
                if ch != "\n":
                    if visible >= limit:
                        return out
                    visible += 1
                cls._append_part(out, ch, style)
        return out

    @staticmethod
    def _visible_len(parts: list[TextPart]) -> int:
        return sum(1 for ch in TextState._parts_text(parts) if ch != "\n")

    @staticmethod
    def _parts_text(parts: list[TextPart]) -> str:
        return "".join(str(part.get("text") or "") for part in parts)

    @classmethod
    def _slice_parts(cls, parts: list[TextPart], start: int, end: int) -> list[TextPart]:
        out: list[TextPart] = []
        pos = 0
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

    @staticmethod
    def _append_part(parts: list[TextPart], text: str, style: typing.Optional[str]) -> None:
        if not text:
            return None
        if parts and parts[-1].get("style") == style:
            parts[-1]["text"] = str(parts[-1].get("text") or "") + text
            return None
        parts.append({"text": text, "style": style})

    @classmethod
    def _extend_parts(cls, target: list[TextPart], source: list[TextPart]) -> None:
        for part in source:
            cls._append_part(target, str(part.get("text") or ""), part.get("style"))

    @staticmethod
    def _trim_tail(parts: list[str], line_start: int, count: int) -> int:
        removed = 0
        while count > 0 and len(parts) > line_start:
            parts.pop()
            count -= 1
            removed += 1
        return removed

    @staticmethod
    def _trim_visible_tail(parts: list[str], limit: int, reserve: int) -> None:
        keep = max(0, limit - reserve)
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
