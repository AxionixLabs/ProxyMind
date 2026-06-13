# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import typing
from loguru import logger
from mind_nova import const


class StreamRecordWriter(object):
    """只负责把显示记录按行落盘。"""

    def __init__(self, log_file: str) -> None:
        self.log_file = log_file
        self.buffer: str = ""
        self.fp: typing.Optional[typing.TextIO] = None
        self.at_line_start: bool = True
        self.trailing_newlines: int = 0
        self.last_display: str | None = None

    async def open(self) -> None:
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
        self.flush()
        if self.fp:
            try:
                self.fp.flush()
            finally:
                self.fp.close()
            self.fp = None

    def _normalize_display_text(self, text: str, *, display: str) -> str:
        if display == "block":
            return self._normalize_block_text(text)
        return self._normalize_stream_text(text)

    def _normalize_block_text(self, text: str) -> str:
        body = text.strip("\n")
        if not body:
            return ""

        prefix = self._segment_prefix(for_display="block")
        return f"{prefix}{body}\n"

    def _normalize_stream_text(self, text: str) -> str:
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
        if self.last_display is None:
            return ""

        if self.last_display == "stream" and for_display == "stream":
            return ""

        if self.trailing_newlines >= 2:
            return ""

        if for_display == "stream" and incoming_text and incoming_text.startswith("\n"):
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


if __name__ == '__main__':
    pass
