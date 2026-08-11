# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import unicodedata
from urllib.parse import urlsplit
from .models import (
    StyledBlock,
    TextSpan,
    TextStyle
)
from .text_layout import text_display_width

TextWidth = typing.Callable[[str], int]

_ESC = "\x1b"
_BEL = "\x07"
_CSI = "\x9b"
_ST  = "\x9c"

_CONTROL_STRING_STARTS = {
    "\x90",  # DCS
    "\x98",  # SOS
    "\x9d",  # OSC
    "\x9e",  # PM
    "\x9f",  # APC
}

_ESC_CONTROL_STRING_STARTS = {"P", "X", "]", "^", "_"}


class TerminalTextFilter(object):
    """按流式边界移除终端控制序列并生成稳定显示文本。"""

    def __init__(
        self,
        *,
        tab_size: int = 8,
        measure_width: TextWidth | None = None,
    ) -> None:
        self.tab_size      = max(1, int(tab_size or 1))
        self.measure_width = measure_width or text_display_width

        self._state: str       = "text"
        self._column: int      = 0
        self._pending_cr: bool = False

    def feed(self, value: typing.Any) -> str:
        """过滤一段文本，并保留未完成控制序列的解析状态。"""
        text = "" if value is None else str(value)
        if not text:
            return ""

        out: list[str] = []

        index: int = 0
        while index < len(text):
            char = text[index]

            if self._state == "text":
                if self._pending_cr:
                    self._pending_cr = False
                    self._append_newline(out)
                    if char == "\n":
                        index += 1
                        continue

                if char == "\r":
                    self._pending_cr = True
                elif char == "\n":
                    self._append_newline(out)
                elif char == "\t":
                    count = self.tab_size - (self._column % self.tab_size)
                    self._append_text(out, " " * count)
                elif char == _ESC:
                    self._state = "escape"
                elif char == _CSI:
                    self._state = "csi"
                elif char in _CONTROL_STRING_STARTS:
                    self._state = "control_string"
                elif char == _ST or self._control_character(char):
                    pass
                else:
                    self._append_text(out, char)

                index += 1
                continue

            if self._state == "escape":
                if char == "[":
                    self._state = "csi"
                elif char in _ESC_CONTROL_STRING_STARTS:
                    self._state = "control_string"
                elif char == _ESC:
                    self._state = "escape"
                elif "\x20" <= char <= "\x2f":
                    self._state = "escape_intermediate"
                elif "\x30" <= char <= "\x7e":
                    self._state = "text"
                else:
                    self._state = "text"
                    continue

                index += 1
                continue

            if self._state == "escape_intermediate":
                if char == _ESC:
                    self._state = "escape"
                elif "\x30" <= char <= "\x7e":
                    self._state = "text"
                index += 1
                continue

            if self._state == "csi":
                if char == _ESC:
                    self._state = "escape"
                elif char == _CSI:
                    self._state = "csi"
                elif "\x40" <= char <= "\x7e":
                    self._state = "text"
                index += 1
                continue

            if self._state == "control_string":
                if char == _BEL or char == _ST:
                    self._state = "text"
                elif char == _ESC:
                    self._state = "control_string_escape"
                index += 1
                continue

            if self._state == "control_string_escape":
                if char == "\\" or char == _ST:
                    self._state = "text"
                elif char != _ESC:
                    self._state = "control_string"
                index += 1

        return "".join(out)

    def finish(self) -> str:
        """收束当前文本边界，丢弃未完成控制序列。"""
        out: list[str] = []
        if self._pending_cr:
            self._append_newline(out)
        self._pending_cr = False
        self._state = "text"
        return "".join(out)

    def reset(self) -> None:
        """丢弃解析状态并从新的显示行开始。"""
        self._state = "text"
        self._column = 0
        self._pending_cr = False

    def _append_newline(self, out: list[str]) -> None:
        out.append("\n")
        self._column = 0

    def _append_text(self, out: list[str], text: str) -> None:
        if not text:
            return None
        out.append(text)
        self._column += max(0, int(self.measure_width(text)))

    @staticmethod
    def _control_character(char: str) -> bool:
        return char == "\x7f" or unicodedata.category(char) == "Cc"


def sanitize_terminal_text(
    value: typing.Any,
    *,
    tab_size: int = 8,
    measure_width: TextWidth | None = None
) -> str:
    """把完整文本转换为不可执行的终端显示文本。"""
    text_filter = TerminalTextFilter(
        tab_size=tab_size,
        measure_width=measure_width,
    )
    return f"{text_filter.feed(value)}{text_filter.finish()}"


def sanitize_terminal_line(
    value: typing.Any,
    *,
    tab_size: int = 8,
    measure_width: TextWidth | None = None,
) -> str:
    """把外部文本转换为不可执行的单行终端文本。"""
    return " ".join(sanitize_terminal_text(
        value,
        tab_size=tab_size,
        measure_width=measure_width,
    ).split())


def sanitize_text_spans(
    spans: typing.Iterable[TextSpan],
    *,
    tab_size: int = 8,
    measure_width: TextWidth | None = None
) -> tuple[TextSpan, ...]:
    """清理一组样式片段，并保留跨片段控制序列状态。"""
    text_filter = TerminalTextFilter(
        tab_size=tab_size,
        measure_width=measure_width,
    )

    out: list[TextSpan]        = []
    last_style: TextStyle      = TextStyle()
    last_hyperlink: str | None = None

    for span in spans:
        last_style     = span.style
        last_hyperlink = sanitize_terminal_hyperlink(span.hyperlink)

        _append_span(
            out,
            text_filter.feed(span.text),
            span.style,
            last_hyperlink,
        )

    _append_span(out, text_filter.finish(), last_style, last_hyperlink)
    return tuple(out)


def sanitize_terminal_hyperlink(value: typing.Any) -> str | None:
    """返回可安全写入 OSC 8 的绝对链接。"""
    if not isinstance(value, str):
        return None

    url = value.strip()
    if not url or len(url) > 4096:
        return None
    if any(
        ord(char) < 32 or 127 <= ord(char) <= 159
        for char in url
    ):
        return None

    try:
        scheme = urlsplit(url).scheme.casefold()
    except ValueError:
        return None

    return url if scheme in {"file", "http", "https", "mailto"} else None


def sanitize_styled_block(
    block: StyledBlock,
    *,
    tab_size: int = 8,
    measure_width: TextWidth | None = None
) -> StyledBlock:
    """返回保持展示属性不变的安全文本块。"""
    plain_text = sanitize_terminal_text(
        block.plain_text,
        tab_size=tab_size,
        measure_width=measure_width,
    )
    spans = sanitize_text_spans(
        block.spans,
        tab_size=tab_size,
        measure_width=measure_width,
    ) if block.spans else ()

    if plain_text == block.plain_text and spans == block.spans:
        return block

    return StyledBlock(
        plain_text=plain_text,
        spans=spans,
        preserve_spans=block.preserve_spans,
        direct=block.direct,
    )


def _append_span(
    out: list[TextSpan],
    text: str,
    style: TextStyle,
    hyperlink: str | None
) -> None:
    """追加非空片段并合并相邻的相同样式。"""
    if not text:
        return None

    if (
        out
        and out[-1].style == style
        and out[-1].hyperlink == hyperlink
    ):
        previous = out[-1]
        out[-1] = TextSpan(f"{previous.text}{text}", style, hyperlink)
        return None

    out.append(TextSpan(text, style, hyperlink))


if __name__ == '__main__':
    pass
