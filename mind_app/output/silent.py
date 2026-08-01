# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from ..stream_io.output_record import StreamRecordWriter
from .session import OutputSession
from .text import (
    TextContentSink,
    TextOutputControl,
    TextOutputState,
    TextPresentationSink,
    TextStream
)


class _DiscardTextStream(TextStream):
    """接收文本写入但不输出到终端。"""

    def write(self, text: str) -> int:
        """丢弃文本并返回已接收字符数。"""
        return len(text)

    def flush(self) -> None:
        """忽略刷新请求。"""
        return None

    def isatty(self) -> bool:
        """声明当前流不是交互终端。"""
        return False


def create_silent_output_session(
    log_file: str,
    *,
    animate: bool = True,
) -> OutputSession:
    """创建只写入记录文件的无终端输出会话。"""
    _ = animate

    discard = _DiscardTextStream()

    state = TextOutputState(
        record_writer=StreamRecordWriter(log_file),
        stdout=discard,
        stderr=discard,
    )

    control = TextOutputControl(state)

    return OutputSession(
        control=control,
        status=control,
        content=TextContentSink(state),
        presentation=TextPresentationSink(state),
    )


if __name__ == '__main__':
    pass
