# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
from dataclasses import dataclass
from mcp import ClientSession
from mcp.types import CallToolResult
from engine.enhancer import Enhancer
from ..stream_ui import StreamUI
from .tool_router import execute_tool


@dataclass(slots=True)
class ToolRunResult(object):
    """统一描述单次工具执行的收束结果。"""
    result: CallToolResult
    ok: bool
    fields: typing.Union[str, dict[str, typing.Any]]
    text: str
    data: typing.Any
    cost_ms: int


def _tool_result_text(fields: typing.Union[str, dict[str, typing.Any], typing.Any]) -> str:
    if isinstance(fields, dict):
        value = fields.get("text")
        return "" if value is None else str(value)
    if fields is None:
        return ""
    return str(fields)


def _tool_result_data(fields: typing.Union[str, dict[str, typing.Any], typing.Any]) -> typing.Any:
    if isinstance(fields, dict):
        return fields.get("data")
    return None


async def run_tool_step(
    session: ClientSession,
    *,
    stream_ui: StreamUI,
    tool_meta: dict[str, dict[str, typing.Any]],
    name: str,
    arguments: dict[str, typing.Any],
    meta: typing.Optional[dict[str, typing.Any]],
    mode: str,
    model_api: dict[str, typing.Any],
    metadata: dict[str, typing.Any],
    enable_progress_notify: bool = False,
    stream_callback: typing.Optional[typing.Callable[[str], typing.Awaitable[None]]] = None,
) -> ToolRunResult:
    """统一执行工具、处理状态动画和结果增强。"""
    started_at = time.time()

    await stream_ui.begin_tool_status()
    try:
        result = await execute_tool(
            session,
            tool_meta=tool_meta,
            name=name,
            arguments=arguments,
            meta=meta,
            enable_progress_notify=enable_progress_notify,
            stream_callback=stream_callback
        )
        ok = not result.isError

        enhancer = Enhancer(session, mode, model_api, metadata)
        fields = await enhancer.enhance(name, result, ok, stream_ui)
    finally:
        await stream_ui.end_status()

    return ToolRunResult(
        result=result,
        ok=ok,
        fields=fields,
        text=_tool_result_text(fields),
        data=_tool_result_data(fields),
        cost_ms=int((time.time() - started_at) * 1000)
    )


if __name__ == '__main__':
    pass
