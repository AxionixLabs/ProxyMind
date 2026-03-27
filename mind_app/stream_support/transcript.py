# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import typing

from mind_nova import const


class TranscriptWriter(object):
    """只负责把转录内容落盘。"""

    def __init__(self, log_file: str) -> None:
        self.log_file = log_file
        self.buffer: str = ""
        self.fp: typing.Optional[typing.TextIO] = None
        self.at_line_start: bool = True

    async def open(self) -> None:
        if self.fp:
            return None

        os.makedirs(os.path.dirname(self.log_file), exist_ok=True)
        self.fp = open(
            self.log_file, "a", encoding=const.CHARSET, buffering=1, newline=""
        )

    def write(self, chunk: typing.Optional[str], *, block: bool = False) -> None:
        if not chunk:
            return None

        delta = str(chunk)
        if block:
            delta = self._normalize_block_text(delta, at_line_start=self.at_line_start)

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

    @staticmethod
    def _normalize_block_text(text: str, *, at_line_start: bool) -> str:
        out = text.strip("\n")
        if not out:
            return "\n" if not at_line_start else ""
        prefix = "" if at_line_start else "\n"
        return f"{prefix}{out}\n\n"


if __name__ == '__main__':
    pass
