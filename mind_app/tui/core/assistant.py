# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import unicodedata
from prompt_toolkit.utils import get_cwidth


class TuiAssistantStream(object):
    """保存当前 assistant 正文、可见位置及待处理段落边界。"""

    def __init__(self) -> None:
        self.text: str = ""
        self.revealed_end: int = 0
        self.boundary_pending: bool = False

    @property
    def active(self) -> bool:
        """返回当前是否存在 assistant 正文。"""
        return bool(self.text)

    @property
    def visible_text(self) -> str:
        """返回当前已经揭示的正文。"""
        return self.text[:self.revealed_end]

    @property
    def pending_length(self) -> int:
        """返回尚未揭示的原文长度。"""
        return max(0, len(self.text) - self.revealed_end)

    @property
    def pending_width(self) -> int:
        """返回尚未揭示正文占用的终端列数。"""
        return max(0, get_cwidth(self.text[self.revealed_end:]))

    def prepare_delta(self, delta: str) -> str:
        """消费段落边界并返回可直接追加的正文增量。"""
        value = str(delta or "")
        if not value:
            return ""
        if not self.boundary_pending:
            return value

        self.boundary_pending = False
        if not self.text or self.text.endswith("\n") or value.startswith("\n"):
            return value
        return f"\n{value}"

    def append(self, delta: str) -> None:
        """追加一段已经处理过边界的正文。"""
        self.text += str(delta or "")

    def reveal(self, cells: int) -> None:
        """按终端显示列推进完整文本单元。"""
        available = max(0, int(cells))
        consumed  = 0

        while available and self.revealed_end < len(self.text):
            unit_end = _next_text_unit_end(self.text, self.revealed_end)
            unit_width = max(
                0,
                get_cwidth(self.text[self.revealed_end:unit_end]),
            )
            if consumed and consumed + unit_width > available:
                break
            self.revealed_end = unit_end
            consumed += unit_width
            if consumed >= available:
                break

    def reveal_all(self) -> None:
        """立即揭示全部已接收正文。"""
        self.revealed_end = len(self.text)

    def mark_boundary(self) -> None:
        """标记下一段正文前需要保留段落边界。"""
        self.boundary_pending = True

    def discard_boundary(self) -> None:
        """消费不再属于当前正文块的待处理边界。"""
        self.boundary_pending = False

    def clear(self) -> None:
        """清空当前正文和段落边界。"""
        self.text             = ""
        self.revealed_end     = 0
        self.boundary_pending = False


def _next_text_unit_end(text: str, start: int) -> int:
    """返回下一个组合文本单元的结束位置。"""
    limit = len(text)
    index = min(limit, max(0, int(start)))
    if index >= limit:
        return limit

    first = text[index]
    index += 1

    if first == "\r" and index < limit and text[index] == "\n":
        return index + 1
    if _is_regional_indicator(first):
        if index < limit and _is_regional_indicator(text[index]):
            index += 1
        return index

    while index < limit:
        char = text[index]
        if _extends_text_unit(char):
            index += 1
            continue
        if char == "\u200d" and index + 1 < limit:
            index += 2
            continue
        break
    return index


def _extends_text_unit(char: str) -> bool:
    """判断字符是否延续前一个组合文本单元。"""
    codepoint = ord(char)
    return bool(
        unicodedata.combining(char)
        or unicodedata.category(char).startswith("M")
        or 0xFE00 <= codepoint <= 0xFE0F
        or 0xE0100 <= codepoint <= 0xE01EF
        or 0x1F3FB <= codepoint <= 0x1F3FF
    )


def _is_regional_indicator(char: str) -> bool:
    """判断字符是否为区域指示符。"""
    return 0x1F1E6 <= ord(char) <= 0x1F1FF


if __name__ == '__main__':
    pass
