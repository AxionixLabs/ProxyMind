# -*- coding: utf-8 -*-

import typing
from abc import (
    ABC,
    abstractmethod,
)
from dataclasses import dataclass

from .content import ContentSink
from .presentation import (
    TextSpan,
    TextStyle,
)

OutputDisplay = typing.Literal["stream", "block"]

STREAM_OUTPUT: typing.Final[OutputDisplay] = "stream"
BLOCK_OUTPUT: typing.Final[OutputDisplay] = "block"

PresentationViewT = typing.TypeVar(
    "PresentationViewT",
    contravariant=True,
)


class OutputControlPort(ABC):
    """描述单轮运行需要的输出生命周期和审计能力。"""

    @abstractmethod
    async def open(self) -> None:
        """打开输出会话。"""
        ...

    @abstractmethod
    async def stop(self, *, blink: bool = True) -> None:
        """停止输出会话并释放资源。"""
        ...

    @abstractmethod
    async def record_hidden_output(self, text: str) -> None:
        """记录不直接展示的输出内容。"""
        ...

    @abstractmethod
    def record_tool_arguments(
        self,
        name: str,
        arguments: dict[str, typing.Any],
        *,
        call_id: str | None = None,
    ) -> None:
        """记录工具参数审计信息。"""
        ...


class OutputStatusPort(ABC):
    """描述单轮流式事件使用的局部状态展示能力。"""

    @abstractmethod
    async def begin_tool_status(self) -> None:
        """启动通用工具状态。"""
        ...

    @abstractmethod
    async def begin_custom_tool_status(self, text: str | None) -> None:
        """启动自定义工具状态。"""
        ...

    @abstractmethod
    async def begin_reply_wait_status(
        self,
        text: str | None = "Thinking",
        *,
        delay_sec: float = 0.28,
        animate_after_sec: float | None = None,
    ) -> None:
        """启动回复等待状态。"""
        ...

    @abstractmethod
    async def end_status(self, *, immediate: bool = False) -> None:
        """结束当前状态。"""
        ...


class OutputPort(OutputControlPort, OutputStatusPort):
    """描述终端渲染适配器需要的完整输出能力。"""

    @property
    @abstractmethod
    def terminal_width(self) -> int | None:
        """返回当前终端宽度。"""
        ...

    @property
    @abstractmethod
    def terminal_height(self) -> int | None:
        """返回当前终端高度。"""
        ...

    @abstractmethod
    async def prepare_external_output(self) -> None:
        """准备输出外部内容。"""
        ...

    @abstractmethod
    async def settle_stream(self) -> None:
        """同步当前流式正文。"""
        ...

    @abstractmethod
    def mark_stream_boundary(self) -> None:
        """标记下一段流式边界。"""
        ...

    @abstractmethod
    async def feed(
        self,
        chunk: str | None,
        *,
        echo: bool = True,
        display: OutputDisplay = STREAM_OUTPUT,
        display_chunk: str | None = None,
        display_style: TextStyle | None = None,
        display_parts: list[TextSpan] | None = None,
        preserve_display_parts: bool = False,
    ) -> None:
        """追加一段流式或块状输出。"""
        ...

    @abstractmethod
    async def print_block(
        self,
        chunk: str | None,
        *,
        display_parts: list[TextSpan] | None = None,
    ) -> None:
        """直接输出块文本。"""
        ...

    @abstractmethod
    def flush(self) -> None:
        """刷新输出缓冲区。"""
        ...


class OutputPresentationPort(typing.Protocol[PresentationViewT]):
    """接收由 application view 生成的结构化输出。"""

    async def emit(self, view: PresentationViewT) -> None:
        """发送一项结构化输出。"""
        ...


@dataclass(frozen=True, slots=True)
class OutputSession(typing.Generic[PresentationViewT]):
    """聚合单轮运行所需的输出控制、内容和展示端口。"""

    control: OutputControlPort
    status: OutputStatusPort
    content: ContentSink
    presentation: OutputPresentationPort[PresentationViewT]
    show_hook_lifecycle: bool = False


class OutputSessionFactory(typing.Protocol[PresentationViewT]):
    """定义按记录路径创建输出会话的工厂端口。"""

    def __call__(
        self,
        log_file: str,
        *,
        animate: bool = True,
    ) -> OutputSession[PresentationViewT]:
        """创建绑定输出记录和前端展示端口的会话。"""
        ...


__all__ = (
    "BLOCK_OUTPUT",
    "ContentSink",
    "OutputControlPort",
    "OutputDisplay",
    "OutputPort",
    "OutputPresentationPort",
    "OutputSession",
    "OutputSessionFactory",
    "OutputStatusPort",
    "STREAM_OUTPUT",
)
