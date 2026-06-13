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

_COMMON_PROMOTED_RESULT_KEYS = (
    "path",
    "source_path",
    "target_path",
    "cwd",
    "command",
    "resolved_command",
    "error",
    "exit_code",
    "timed_out",
    "elapsed_ms",
    "line",
    "target_line",
    "hunk",
    "expected",
    "actual",
    "found",
    "expected_sha256",
    "current_sha256",
    "actual_sha256",
    "sha256",
    "sha256_before",
    "sha256_after",
    "size",
    "max_bytes",
    "changed",
    "replacements",
    "hunk_count",
    "file_count",
    "match_count",
    "ok_count",
    "fail_count",
    "truncated",
    "stdout_truncated",
    "stderr_truncated",
    "execution_target",
    "requires_cloud_sandbox",
    "cloud_sandbox_supported",
)

_OUTPUT_PROMOTED_TOOLS = {
    "shell_command"
}

_OUTPUT_PROMOTED_RESULT_KEYS = (
    "stdout",
    "stderr"
)


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


def _native_broadcast_data(fields: dict[str, typing.Any]) -> dict[str, typing.Any]:
    data = fields.get("data")
    return data if isinstance(data, dict) else {}


def _native_result_items(fields: dict[str, typing.Any]) -> list[dict[str, typing.Any]]:
    """提取 broadcast 包装内的原生工具结果项。"""
    data = _native_broadcast_data(fields)
    results = data.get("results")
    if not isinstance(results, list):
        return []
    return [item for item in results if isinstance(item, dict)]


def _native_result_failed(item: dict[str, typing.Any]) -> bool:
    if item.get("ok") is False:
        return True
    item_data = item.get("data")
    return isinstance(item_data, dict) and item_data.get("ok") is False


def _first_native_result_item(fields: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """优先取失败结果；没有失败时取第一个原生工具结果。"""
    items = _native_result_items(fields)
    if not items:
        return {}
    for item in items:
        if _native_result_failed(item):
            return item
    return items[0]


def _first_native_result_data(fields: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """从 broadcast 结果中取出代表性的原生编码执行数据。"""
    item = _first_native_result_item(fields)
    if item:
        item_data = item.get("data")
        if isinstance(item_data, dict):
            return item_data

    data = _native_broadcast_data(fields)

    direct_keys = {
        "ok",
        "stdout",
        "stderr",
        "exit_code"
    }

    return data if any(key in data for key in direct_keys) else {}


def _promote_if_present(
    normalized: dict[str, typing.Any],
    payload: dict[str, typing.Any],
    keys: tuple[str, ...]
) -> None:
    for key in keys:
        if key in payload:
            normalized[key] = payload[key]


def _single_batch_result_data(
    payload: dict[str, typing.Any]
) -> dict[str, typing.Any]:
    """单元素批量结果可沿用单命令顶层字段，方便模型读取 stdout/stderr。"""
    results = payload.get("results")
    if not isinstance(results, list) or len(results) != 1:
        return {}

    item = results[0]
    if not isinstance(item, dict):
        return {}
    result = item.get("result")
    if not isinstance(result, dict):
        return {}
    data = result.get("data")
    return data if isinstance(data, dict) else {}


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

    normalized = dict(fields)
    data = _native_broadcast_data(fields)
    item = _first_native_result_item(fields)

    if isinstance(data.get("ok"), bool):
        normalized["ok"] = data["ok"]
    elif isinstance(payload.get("ok"), bool):
        normalized["ok"] = payload["ok"]

    normalized["tool"] = str(data.get("tool") or name)

    if item.get("agent_id"):
        normalized["agent_id"] = item["agent_id"]

    summary = data.get("summary")
    if isinstance(summary, dict):
        normalized["result_summary"] = summary

    _promote_if_present(normalized, payload, _COMMON_PROMOTED_RESULT_KEYS)

    representative = _single_batch_result_data(payload)
    if representative:
        _promote_if_present(normalized, representative, _COMMON_PROMOTED_RESULT_KEYS)

    if name in _OUTPUT_PROMOTED_TOOLS:
        _promote_if_present(normalized, representative or payload, _OUTPUT_PROMOTED_RESULT_KEYS)

    return normalized


async def run_tool_step(
    session: McpSessionLike,
    *,
    stream_ui: StreamUI,
    tool_meta: dict[str, dict[str, typing.Any]],
    name: str,
    arguments: dict[str, typing.Any],
    meta: typing.Optional[dict[str, typing.Any]],
    mode: str,
    pref_config: dict[str, typing.Any],
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

        enhancer = Enhancer(session, mode, pref_config, metadata)
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
