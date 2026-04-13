# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from loguru import logger


def supports_tool_progress(name: str) -> bool:
    """判断工具是否启用 MCP 进度通知展示。"""
    return str(name or "").startswith("coding_")


async def emit_tool_progress(
    *,
    tool_name: str,
    progress: float,
    total: float | None,
    message: str | None,
    stream_callback: typing.Optional[typing.Callable[[str], typing.Awaitable[None]]] = None
) -> None:
    """统一消费工具过程中的 MCP 进度通知。"""
    text = str(message or "").strip()
    if not text:
        text = (
            f"{tool_name} progress={progress}/{total}"
            if total is not None else
            f"{tool_name} progress={progress}"
        )

    if stream_callback is not None:
        await stream_callback(text)
        return None

    logger.info(text)


if __name__ == '__main__':
    pass
