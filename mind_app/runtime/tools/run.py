# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
import asyncio
import functools
from dataclasses import dataclass
from mind_app.mcp.contracts import McpSessionLike
from mind_app.runtime.execution import ToolInvocation
from mind_app.mcp.tool_result import (
    normalize_call_tool_result,
    normalize_tool_fields
)
from mind_app.presentation.contracts import PresentationSink
from engine.enhance import enhance_result
from ...output import (
    OutputControlPort,
    OutputStatusPort
)
from .enhance_reporter import ToolEnhanceReporter
from .progress import show_tool_progress
from .router import execute_tool
from engine.observability import (
    observe,
    observe_exception
)

_COMMON_PROMOTED_RESULT_KEYS = (
    "path",
    "source_path",
    "target_path",
    "cwd",
    "command",
    "resolved_command",
    "error",
    "session_id",
    "status",
    "pid",
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
)

_OUTPUT_PROMOTED_TOOLS = {
    "shell_command",
    "exec_command",
    "write_stdin"
}

_OUTPUT_PROMOTED_RESULT_KEYS = (
    "output",
    "stdout",
    "stderr",
    "output_lines"
)


@dataclass(slots=True)
class ToolRunResult:
    """统一描述单次工具执行的收束结果。"""
    result: typing.Any
    ok: bool
    fields: dict[str, typing.Any]
    text: str
    data: typing.Any
    cost_ms: int


def _tool_result_data(fields: typing.Union[str, dict[str, typing.Any], typing.Any]) -> typing.Any:
    """从工具结果字段中提取结构化数据。"""
    if isinstance(fields, dict):
        return fields.get("data")
    return None


def _tool_result_data_map(fields: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """提取工具结果中的 data 字段。"""
    data = fields.get("data")
    return data if isinstance(data, dict) else {}


def _tool_payload(fields: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """从单次工具结果中取出业务 payload。"""
    return _tool_result_data_map(fields)


def _promote_if_present(
    normalized: dict[str, typing.Any],
    payload: dict[str, typing.Any],
    keys: tuple[str, ...]
) -> None:
    """将存在的结果字段提升到顶层结果。"""
    for key in keys:
        if key in payload:
            normalized[key] = payload[key]


def normalize_tool_result_fields(
    name: str,
    fields: dict[str, typing.Any]
) -> dict[str, typing.Any]:
    """为 synthetic 原生工具调用补充稳定的顶层结果字段。"""
    payload = _tool_payload(fields)
    if not payload:
        return fields

    normalized = dict(fields)

    if isinstance(fields.get("ok"), bool):
        normalized["ok"] = fields["ok"]

    normalized["tool"] = str(fields.get("tool") or name)

    if fields.get("target"):
        normalized["target"] = fields["target"]

    _promote_if_present(normalized, payload, _COMMON_PROMOTED_RESULT_KEYS)

    if name in _OUTPUT_PROMOTED_TOOLS:
        _promote_if_present(normalized, payload, _OUTPUT_PROMOTED_RESULT_KEYS)

    return normalized


def server_tool_output_result(
    name: str,
    event: dict[str, typing.Any]
) -> ToolRunResult:
    """把服务端回灌的 tool.output 事件转换成展示层结果对象。"""
    raw_fields = _server_output_fields(event)
    ok         = _server_output_ok(event, raw_fields)
    normalized = normalize_tool_fields(raw_fields, ok=ok)
    fields     = normalize_tool_result_fields(name, normalized.fields)
    cost_ms = _server_output_cost_ms(event)

    return ToolRunResult(
        result=fields,
        ok=ok,
        fields=fields,
        text=normalized.display_text,
        data=_tool_result_data(fields),
        cost_ms=cost_ms
    )


def _server_output_fields(event: dict[str, typing.Any]) -> typing.Union[str, dict[str, typing.Any]]:
    """从服务端工具输出事件中提取结果字段。"""
    for key in ("result", "fields", "output"):
        value = event.get(key)
        if isinstance(value, dict):
            return _server_output_dict_fields(value, event)
        if isinstance(value, str):
            return value

    data = event.get("data")
    if isinstance(data, dict):
        text = event.get("text")
        return {
            "ok"          : bool(event["ok"]) if isinstance(event.get("ok"), bool) else True,
            "text"        : str(text) if text is not None else "",
            "attachments" : event.get("attachments") if isinstance(event.get("attachments"), list) else [],
            "data"        : data
        }

    text = event.get("text")
    if text is not None:
        return str(text)

    return {
        "ok"          : bool(event["ok"]) if isinstance(event.get("ok"), bool) else True,
        "text"        : "tool.output received",
        "attachments" : [],
        "data"        : {}
    }


def _server_output_dict_fields(
    value: dict[str, typing.Any],
    event: dict[str, typing.Any]
) -> dict[str, typing.Any]:
    """规范化服务端工具输出中的字典结果。"""
    fields = dict(value)

    if "text" not in fields and event.get("text") is not None:
        fields["text"] = str(event.get("text") or "")

    if "data" not in fields:
        event_data = event.get("data")
        if isinstance(event_data, dict):
            fields["data"] = event_data
        elif any(key in fields for key in ("ok", "stdout", "stderr", "exit_code", "results")):
            fields["data"] = dict(fields)

    for key in ("ok", "tool", "args", "target"):
        if key not in fields and key in event:
            fields[key] = event[key]

    if "attachments" not in fields and isinstance(event.get("attachments"), list):
        fields["attachments"] = event.get("attachments")

    return fields


def _server_output_ok(
    event: dict[str, typing.Any],
    fields: typing.Union[str, dict[str, typing.Any]]
) -> bool:
    """根据事件和结果字段判断服务端工具输出是否成功。"""
    if isinstance(event.get("ok"), bool):
        return bool(event["ok"])
    if isinstance(fields, dict):
        if isinstance(fields.get("ok"), bool):
            return bool(fields["ok"])

    return True


def _server_output_cost_ms(event: dict[str, typing.Any]) -> int:
    """从服务端工具输出事件中提取耗时毫秒数。"""
    for key in ("cost_ms", "elapsed_ms"):
        value = event.get(key)
        if isinstance(value, (int, float)):
            return max(0, int(value))

    data = event.get("data")
    if isinstance(data, dict):
        value = data.get("elapsed_ms")
        if isinstance(value, (int, float)):
            return max(0, int(value))

    return 0


async def run_tool_step(
    session: McpSessionLike,
    *,
    output_control: OutputControlPort,
    status_control: OutputStatusPort,
    presentation: PresentationSink,
    tools: list[dict[str, typing.Any]],
    invocation: ToolInvocation,
    pref_config: dict[str, typing.Any],
    enable_progress_notify: bool = False,
    status_text: typing.Optional[str] = None
) -> ToolRunResult:
    """统一执行工具、处理状态动画和结果增强。"""
    started_at = time.time()
    name       = invocation.name

    observe(
        "tool.start",
        tool=name,
        call_id=invocation.call_id,
        turn_id=invocation.turn.turn_id,
        agent_id=invocation.turn.agent.agent_id,
        cid=invocation.turn.cid,
        sid=invocation.turn.sid,
    )

    try:
        if status_text:
            await status_control.begin_custom_tool_status(status_text)
        else:
            await status_control.begin_tool_status()
        try:
            result = await execute_tool(
                session,
                tools=tools,
                invocation=invocation,
                pref_config=pref_config,
                enable_progress_notify=enable_progress_notify,
                stream_callback=functools.partial(
                    show_tool_progress,
                    presentation,
                    source="tool",
                    tool_name=name,
                ),
            )
            ok = not result.isError

            normalized = normalize_call_tool_result(result)

            fields = await enhance_result(
                pref_config=pref_config,
                name=name,
                result_fields=normalized.fields,
                ok=ok,
                reporter=ToolEnhanceReporter(
                    output_control,
                    status_control,
                    presentation,
                    tool_name=name,
                )
            )
            if isinstance(fields.get("ok"), bool):
                ok = bool(fields["ok"])
            normalized = normalize_tool_fields(
                fields,
                ok=ok,
                display_fallback=normalized.display_text,
            )
            fields = normalize_tool_result_fields(name, normalized.fields)

        finally:
            await status_control.end_status()
    except asyncio.CancelledError:
        observe(
            "tool.interrupted",
            level="WARNING",
            tool=name,
            call_id=invocation.call_id,
            turn_id=invocation.turn.turn_id,
            elapsed_ms=int((time.time() - started_at) * 1000),
        )
        raise
    except BaseException as error:
        observe_exception(
            "tool.failed",
            error,
            tool=name,
            call_id=invocation.call_id,
            turn_id=invocation.turn.turn_id,
            elapsed_ms=int((time.time() - started_at) * 1000),
        )
        raise

    cost_ms = int((time.time() - started_at) * 1000)

    observe(
        "tool.complete",
        tool=name,
        call_id=invocation.call_id,
        turn_id=invocation.turn.turn_id,
        ok=ok,
        elapsed_ms=cost_ms,
    )

    return ToolRunResult(
        result=result,
        ok=ok,
        fields=fields,
        text=normalized.display_text,
        data=_tool_result_data(fields),
        cost_ms=cost_ms
    )


if __name__ == '__main__':
    pass
