# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from prompt_toolkit.utils import get_cwidth
from .render import next_text_unit_end


class TuiAssistantStream(object):
    """保存当前 assistant 正文、可见位置及待处理段落边界。"""

    def __init__(self) -> None:
        self.text: str              = ""
        self.revealed_end: int      = 0
        self.boundary_pending: bool = False
        self._pending_width: int    = 0

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
        return self._pending_width

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
        value = str(delta or "")
        self.text += value
        self._pending_width += max(0, get_cwidth(value))

    def reveal(self, cells: int) -> None:
        """按终端显示列推进完整文本单元。"""
        available = max(0, int(cells))
        consumed  = 0

        while available and self.revealed_end < len(self.text):
            unit_end = next_text_unit_end(self.text, self.revealed_end)

            unit_width = max(
                0,
                get_cwidth(self.text[self.revealed_end:unit_end]),
            )
            if consumed and consumed + unit_width > available:
                break

            self.revealed_end = unit_end
            consumed += unit_width
            self._pending_width = max(0, self._pending_width - unit_width)

            if consumed >= available:
                break

    def reveal_all(self) -> None:
        """立即揭示全部已接收正文。"""
        self.revealed_end   = len(self.text)
        self._pending_width = 0

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
        self._pending_width   = 0


if __name__ == '__main__':
    pass
