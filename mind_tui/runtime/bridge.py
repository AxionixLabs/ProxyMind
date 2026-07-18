# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from collections.abc import (
    Awaitable,
    Callable
)
from ..events import AppEvent

EmitEvent = Callable[[AppEvent], Awaitable[None]]


class RuntimeDisplayBridge:
    """把工具运行时的展示调用转换为结构化界面事件。"""

    BLOCK  = "block"
    STREAM = "stream"

    def __init__(self, emit: EmitEvent) -> None:
        """初始化事件输出函数。"""
        self._emit = emit

    async def feed(
        self,
        chunk: str | None,
        *,
        echo: bool = True,
        display: str = STREAM,
        display_chunk: str | None = None,
        **kwargs: typing.Any
    ) -> None:
        """把运行时文本转换为可渲染的内容块。"""
        _ = display, kwargs

        if not echo:
            return

        text = str(display_chunk if display_chunk is not None else chunk or "")
        if text:
            await self._emit(
                AppEvent("display.block", text=text, payload={"kind": "trace"})
            )

    async def begin_tool_status(self) -> None:
        """显示通用工具执行状态。"""
        await self._status("Running tool")

    async def begin_custom_tool_status(self, text: str | None) -> None:
        """显示自定义工具执行状态。"""
        await self._status(str(text or "Running tool"))

    async def begin_code_status(self, text: str | None, **kwargs: typing.Any) -> None:
        """显示编码工具执行状态。"""
        _ = kwargs
        await self._status(str(text or "Coding"))

    async def begin_loop_status(self, text: str | None) -> None:
        """显示步骤计划执行状态。"""
        await self._status(str(text or "Running plan"))

    async def update_loop_status_summary(self, text: str | None) -> None:
        """更新步骤计划执行状态。"""
        await self._status(str(text or "Running plan"))

    async def begin_heal_status(self) -> None:
        """显示元素修复状态。"""
        await self._status("Healing")

    async def update_heal_status_summary(self, text: str | None) -> None:
        """更新元素修复状态。"""
        await self._status(str(text or "Healing"))

    async def end_status(self, **kwargs: typing.Any) -> None:
        """清理运行时展示状态。"""
        _ = kwargs
        await self._status("")

    def record_tool_arguments(
        self,
        name: str,
        arguments: dict[str,typing.Any],
        *,
        call_id: str | None = None
    ) -> None:
        """保留工具展示协议所需的参数记录接口。"""
        _ = name, arguments, call_id

    async def _status(self, text: str) -> None:
        """发布固定状态区更新事件。"""
        await self._emit(AppEvent("status.update", text=text))


if __name__ == '__main__':
    pass
