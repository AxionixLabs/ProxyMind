# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import typing
from engine.observability import observe_exception
from mind_nova import const
from mind_app.stream_state.boundary import OutputBoundaryState


class StreamRecordWriter(object):
    """只负责把显示记录按行落盘。"""

    def __init__(self, log_file: str) -> None:
        """初始化输出记录器状态。"""
        self.log_file = log_file

        self.buffer: str                        = ""
        self.fp: typing.Optional[typing.TextIO] = None
        self.boundary: OutputBoundaryState      = OutputBoundaryState(stream_display="stream")

    @property
    def at_line_start(self) -> bool:
        return self.boundary.at_line_start

    @at_line_start.setter
    def at_line_start(self, value: bool) -> None:
        self.boundary.at_line_start = bool(value)

    @property
    def trailing_newlines(self) -> int:
        return self.boundary.trailing_newlines

    @trailing_newlines.setter
    def trailing_newlines(self, value: int) -> None:
        self.boundary.trailing_newlines = max(0, int(value or 0))

    @property
    def last_display(self) -> str | None:
        return self.boundary.last_display

    @last_display.setter
    def last_display(self, value: str | None) -> None:
        self.boundary.last_display = value

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
            observe_exception(
                "stream_record.disabled",
                exc,
                level="WARNING",
                path=self.log_file,
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

        self.boundary.observe_display(
            display="block" if block else "stream",
            text=delta
        )

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

        self.boundary.observe_raw(text)

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
        return self.boundary.prefix(for_display=for_display, incoming_text=incoming_text)


if __name__ == '__main__':
    pass
