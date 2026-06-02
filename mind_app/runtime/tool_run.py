# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
from dataclasses import dataclass
from mind_app.mcp import McpSessionLike
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


def _first_native_result_data(fields: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """从 broadcast 结果中取出第一个原生编码执行数据。"""
    data = fields.get("data")
    if not isinstance(data, dict):
        return {}

    direct = data if any(key in data for key in {"stdout", "stderr", "exit_code"}) else {}
    results = data.get("results")
    if isinstance(results, list):
        for item in results:
            if not isinstance(item, dict):
                continue
            item_data = item.get("data")
            if isinstance(item_data, dict):
                return item_data
    return direct


def normalize_tool_result_fields(
    name: str,
    fields: typing.Union[str, dict[str, typing.Any]]
) -> typing.Union[str, dict[str, typing.Any]]:
    """为 synthetic 原生工具调用补充稳定的顶层结果字段。"""
    if not isinstance(fields, dict):
        return fields

    payload = _first_native_result_data(fields)
    if not payload:
        return fields

    if name == "git_status":
        stdout = str(payload.get("stdout") or "")
        return {
            **fields,
            "git_status": stdout,
            "stdout": stdout
        }

    if name == "shell_exec":
        normalized = dict(fields)
        for key in (
            "exit_code",
            "stdout",
            "stderr",
            "elapsed_ms",
            "timed_out",
            "command",
            "cwd"
        ):
            if key in payload:
                normalized[key] = payload[key]
        return normalized

    return fields


async def run_tool_step(
    session: McpSessionLike,
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
    status_text: typing.Optional[str] = None,
    code_status: bool = False,
) -> ToolRunResult:
    """统一执行工具、处理状态动画和结果增强。"""
    started_at = time.time()

    if code_status:
        await stream_ui.begin_code_status(status_text)
    elif status_text:
        await stream_ui.begin_custom_tool_status(status_text)
    else:
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
        fields = normalize_tool_result_fields(name, fields)
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
