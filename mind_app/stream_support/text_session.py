# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from rich.text import Text
from mind_core.design import Design


class TextStreamSession(object):
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
        self.display_segments: list[dict[str, str]] = []
        self.display_text: str = ""
        self.at_line_start: bool = True

    def append(
        self,
        chunk: typing.Optional[str],
        *,
        display: str = STREAM,
        display_chunk: typing.Optional[str] = None,
        echo: bool = True
    ) -> bool:
        if not echo or not chunk:
            return False

        visible_delta = str(display_chunk) if display_chunk is not None else str(chunk)
        if display == self.BLOCK:
            visible_delta = self._normalize_block_text(
                visible_delta, at_line_start=self.at_line_start
            )

        if not visible_delta:
            return False

        self._append_segment(display, visible_delta)
        visible = self._compose_visible_text()
        animate = (display == self.STREAM and visible.startswith(self.display_text))
        self.display_text = visible
        self.at_line_start = visible_delta.endswith("\n")
        return animate

    def renderable(self) -> Text:
        return Text(self.display_text, style="bold")

    def status_spacer(self) -> str:
        if not self.display_text:
            return ""
        if self.display_text.endswith("\n"):
            return ""
        return "\n"

    def _append_segment(self, display: str, delta: str) -> None:
        if (
            display == self.STREAM
            and self.display_segments
            and self.display_segments[-1]["mode"] == self.STREAM
        ):
            self.display_segments[-1]["text"] += delta
            return None

        self.display_segments.append({"mode": display, "text": delta})

    def _compose_visible_text(self) -> str:
        parts: list[str] = []
        line_limit = self._line_limit()
        block_limit = self._block_limit(line_limit)

        for segment in self.display_segments:
            mode = segment["mode"]
            text = segment["text"]
            if mode == self.BLOCK:
                parts.append(self._render_block(text, block_limit))
                continue
            parts.append(self._render_stream(text, line_limit))

        return "".join(parts)

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

    @staticmethod
    def _normalize_block_text(text: str, *, at_line_start: bool) -> str:
        out = text.strip("\n")
        if not out:
            return "\n" if not at_line_start else ""
        prefix = "" if at_line_start else "\n"
        return f"{prefix}{out}\n\n"

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
