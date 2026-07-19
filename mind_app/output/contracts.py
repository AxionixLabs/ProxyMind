# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

OutputDisplay = typing.Literal["stream", "block"]

STREAM_OUTPUT: typing.Final[OutputDisplay] = "stream"
BLOCK_OUTPUT: typing.Final[OutputDisplay]  = "block"


class OutputPort(typing.Protocol):
    """描述单轮运行需要的输出能力。"""

    async def open(self) -> None:
        """打开输出会话。"""
        ...

    async def stop(self, *, blink: bool = True) -> None:
        """停止输出会话并释放资源。"""
        ...

    async def feed(
        self,
        chunk: typing.Optional[str],
        *,
        echo: bool = True,
        display: OutputDisplay = STREAM_OUTPUT,
        display_chunk: typing.Optional[str] = None,
        display_style: typing.Optional[str] = None,
        display_parts: typing.Optional[list[dict[str, typing.Optional[str]]]] = None,
        preserve_display_parts: bool = False,
    ) -> None:
        """追加一段流式或块状输出。"""
        ...

    async def prepare_external_output(self) -> None:
        """准备输出外部内容。"""
        ...

    async def begin_tool_status(self) -> None:
        """启动通用工具状态。"""
        ...

    async def begin_custom_tool_status(self, text: typing.Optional[str]) -> None:
        """启动自定义工具状态。"""
        ...

    async def begin_reply_wait_status(
        self,
        text: typing.Optional[str] = "thinking",
        *,
        delay_sec: float = 0.28,
        animate_after_sec: float | None = None,
    ) -> None:
        """启动回复等待状态。"""
        ...

    async def end_status(self, *, immediate: bool = False) -> None:
        """结束当前状态。"""
        ...

    async def settle_stream(self) -> None:
        """同步当前流式正文。"""
        ...

    async def print_block(
        self,
        chunk: typing.Optional[str],
        *,
        display_parts: typing.Optional[list[dict[str, typing.Optional[str]]]] = None,
    ) -> None:
        """直接输出块文本。"""
        ...

    def flush(self) -> None:
        """刷新输出缓冲区。"""
        ...

    def mark_stream_boundary(self) -> None:
        """标记下一段流式边界。"""
        ...

    def record_tool_arguments(
        self,
        name: str,
        arguments: dict[str, typing.Any],
        *,
        call_id: typing.Optional[str] = None,
    ) -> None:
        """记录工具参数审计信息。"""
        ...


if __name__ == '__main__':
    pass
