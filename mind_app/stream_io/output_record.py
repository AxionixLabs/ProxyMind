# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import typing
from loguru import logger
from mind_nova import const
from mind_app.stream_state.spacing import segment_prefix


class StreamRecordWriter(object):
    """只负责把显示记录按行落盘。"""

    def __init__(self, log_file: str) -> None:
        """初始化输出记录器状态。"""
        self.log_file = log_file

        self.buffer: str                        = ""
        self.fp: typing.Optional[typing.TextIO] = None
        self.at_line_start: bool                = True
        self.trailing_newlines: int             = 0
        self.last_display: str | None           = None

    async def open(self) -> None:
        """打开记录文件，失败时保持记录器可用。"""
        if self.fp:
            return None

        try:
            dirname = os.path.dirname(self.log_file)
            if dirname:
                os.makedirs(dirname, exist_ok=True)
            self.fp = open(
                self.log_file, "a", encoding=const.CHARSET, buffering=1, newline=""
            )
        except OSError as exc:
            self.fp = None
            logger.warning(
                f"[StreamRecord] disabled file record path={self.log_file!r} "
                f"reason={type(exc).__name__}: {exc}"
            )

    def write(self, chunk: typing.Optional[str], *, block: bool = False) -> None:
        """写入一段显示文本，并按显示类型归一化换行。"""
        if not chunk:
            return None

        delta = self._normalize_display_text(
            str(chunk),
            display="block" if block else "stream"
        )

        if not delta:
            return None

        self.buffer += delta
        while True:
            pos = self.buffer.find("\n")
            if pos < 0:
                break
            line = self.buffer[:pos + 1]
            self.buffer = self.buffer[pos + 1:]
            if self.fp:
                self.fp.write(line)

        self.at_line_start = delta.endswith("\n")
        self.trailing_newlines = self._count_trailing_newlines(delta)
        self.last_display = "block" if block else "stream"

    def write_audit(self, line: typing.Optional[str]) -> None:
        """写入一行审计记录。"""
        if not line:
            return None

        self.flush()
        if self.fp:
            self.fp.write(f"{str(line).rstrip()}\n")
            self.fp.flush()

    def write_raw(self, chunk: typing.Optional[str]) -> None:
        """写入已完成格式化的文本，不再做段间换行归一化。"""
        if not chunk:
            return None

        text = str(chunk)
        self.buffer += text

        while True:
            pos = self.buffer.find("\n")
            if pos < 0:
                break
            line = self.buffer[:pos + 1]
            self.buffer = self.buffer[pos + 1:]
            if self.fp:
                self.fp.write(line)

        self.at_line_start     = text.endswith("\n")
        self.trailing_newlines = self._count_trailing_newlines(text)

    def flush(self) -> None:
        """把缓冲区中未成行的内容写入文件。"""
        if not self.buffer:
            return None

        line = self.buffer
        self.buffer = ""
        if not line.endswith("\n"):
            line += "\n"

        if self.fp:
            self.fp.write(line)
            self.fp.flush()

    async def close(self) -> None:
        """刷新并关闭记录文件。"""
        self.flush()
        if self.fp:
            try:
                self.fp.flush()
            finally:
                self.fp.close()
            self.fp = None

    def _normalize_display_text(self, text: str, *, display: str) -> str:
        """按显示类型归一化待记录文本。"""
        if display == "block":
            return self._normalize_block_text(text)
        return self._normalize_stream_text(text)

    def _normalize_block_text(self, text: str) -> str:
        """归一化块文本记录的段间和尾部换行。"""
        body = text.strip("\n")
        if not body:
            return ""

        prefix = self._segment_prefix(for_display="block")
        return f"{prefix}{body}\n"

    def _normalize_stream_text(self, text: str) -> str:
        """归一化流式文本记录的段间前缀。"""
        if not text:
            return ""

        prefix = self._segment_prefix(for_display="stream", incoming_text=text)
        return f"{prefix}{text}"

    def _segment_prefix(
        self,
        *,
        for_display: str,
        incoming_text: str | None = None
    ) -> str:
        """返回下一段记录文本前需要补充的段间前缀。"""
        return segment_prefix(
            last_display=self.last_display,
            trailing_newlines=self.trailing_newlines,
            stream_display="stream",
            for_display=for_display,
            incoming_text=incoming_text
        )

    @staticmethod
    def _count_trailing_newlines(text: str) -> int:
        """统计文本尾部连续换行数量。"""
        count = 0
        for ch in reversed(text):
            if ch != "\n":
                break
            count += 1
        return count


if __name__ == '__main__':
    pass
