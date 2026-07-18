# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from .cells import (
    TranscriptCell, TranscriptCellKind
)


class TranscriptState:
    """管理类型化会话单元和流式输出边界。"""

    def __init__(self, initial_text: str = "") -> None:
        """初始化会话单元和流式位置。"""
        self._cells: list[TranscriptCell] = []
        self._stream_index: int | None    = None

        initial = _normalize_block(initial_text)
        if initial:
            self._cells.append(TranscriptCell("header", initial))

    @property
    def cells(self) -> tuple[TranscriptCell, ...]:
        """返回当前会话单元快照。"""
        return tuple(self._cells)

    @property
    def stream_open(self) -> bool:
        """返回是否存在未结束的流式单元。"""
        return self._stream_index is not None

    def write_stream(self, text: str) -> bool:
        """追加流式增量并在需要时创建正文单元。"""
        value = _normalize_lines(text)
        if not value:
            return False
        if self._stream_index is None:
            value = value.lstrip("\n")
            if not value:
                return False
            self._cells.append(TranscriptCell("assistant", value, streaming=True))
            self._stream_index = len(self._cells) - 1
            return True

        self._cells[self._stream_index].text += value
        return True

    def finish_stream(self) -> bool:
        """关闭当前流式单元并清理段尾空行。"""
        index = self._stream_index
        if index is None:
            return False
        cell = self._cells[index]
        cell.text = cell.text.rstrip("\n")
        cell.streaming = False
        self._stream_index = None
        if not cell.text:
            self._cells.pop(index)
        return True

    def write_block(self, text: str, *, kind: TranscriptCellKind = "trace") -> bool:
        """追加一个完整内容单元。"""
        value = _normalize_block(text)
        if not value:
            return False
        self.finish_stream()
        self._cells.append(TranscriptCell(kind, value))
        return True

    def render(self) -> str:
        """按统一段间距生成可选择的纯文本。"""
        return "\n\n".join(
            cell.text.strip("\n")
            for cell in self._cells
            if cell.text.strip("\n")
        )


def _normalize_lines(text: str) -> str:
    """将文本归一化为统一换行符。"""
    return str(text or "").replace("\r\n", "\n").replace("\r", "\n")


def _normalize_block(text: str) -> str:
    """归一化完整内容块的段首和段尾。"""
    return _normalize_lines(text).strip("\n")


if __name__ == '__main__':
    pass
