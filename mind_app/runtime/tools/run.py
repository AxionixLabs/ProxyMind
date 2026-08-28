# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
import asyncio
import functools
from dataclasses import dataclass
from mcp import types as mcp_types
from mind_app.mcp.contracts import McpSessionLike
from mind_app.runtime.execution import ToolInvocation
from mind_nova.tool_approval import (
    TOOL_LIFECYCLE_STATUSES,
    ToolLifecycleStatus,
)
from mind_app.mcp.tool_result import (
    normalize_call_tool_result,
    normalize_tool_fields,
    serialize_call_tool_result
)
from mind_app.mcp.tool_store import meta_for_tool
from mind_app.presentation.contracts import PresentationSink
from mind_app.presentation.tool_policy import is_approval_only_tool
from engine.enhance import enhance_result
from ...output import OutputStatusPort
from .enhance_reporter import ToolEnhanceReporter
from .progress import show_tool_progress
from .router import execute_tool
from engine.observability import (
    observe,
    observe_exception
)


@dataclass(slots=True)
class ToolRunResult:
    """统一描述单次工具执行的收束结果。"""
    result: typing.Any
    ok: bool
    fields: dict[str, typing.Any]
    text: str
    data: typing.Any
    hook_response: typing.Any
    cost_ms: int
    status: ToolLifecycleStatus


def _tool_result_data(fields: typing.Union[str, dict[str, typing.Any], typing.Any]) -> typing.Any:
    """从工具结果字段中提取结构化数据。"""
    if isinstance(fields, dict):
        return fields.get("data")
    return None


def _tool_result_data_map(fields: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """提取工具结果中的 data 字段。"""
    data = fields.get("data")
    return data if isinstance(data, dict) else {}


def server_tool_output_result(
    event: dict[str, typing.Any]
) -> ToolRunResult:
    """把服务端回灌的 tool.output 事件转换成展示层结果对象。"""
    raw_fields  = _server_output_fields(event)
    reported_ok = _server_output_ok(event, raw_fields)
    status      = _server_output_status(event)
    ok          = reported_ok if status == "completed" else False
    normalized  = normalize_tool_fields(raw_fields, ok=ok)
    fields      = normalized.fields
    cost_ms     = _server_output_cost_ms(event)

    return ToolRunResult(
        result=fields,
        ok=ok,
        fields=fields,
        text=normalized.display_text,
        data=_tool_result_data(fields),
        hook_response=fields,
        cost_ms=cost_ms,
        status=status,
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
            "ok": bool(event["ok"]) if isinstance(event.get("ok"), bool) else True,
            "text": str(text) if text is not None else "",
            "attachments": event.get("attachments") if isinstance(event.get("attachments"), list) else [],
            "data": data
        }

    text = event.get("text")
    if text is not None:
        return str(text)

    return {
        "ok": bool(event["ok"]) if isinstance(event.get("ok"), bool) else True,
        "text": "tool.output received",
        "attachments": [],
        "data": {}
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


def _server_output_status(
    event: dict[str, typing.Any],
) -> ToolLifecycleStatus:
    """读取服务端工具输出的稳定生命周期状态。"""
    status = str(event.get("status") or "").strip().lower()
    if status in TOOL_LIFECYCLE_STATUSES:
        return typing.cast(ToolLifecycleStatus, status)
    raise ValueError("tool.output requires a supported status")


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

            if is_approval_only_tool(name):
                fields = normalized.fields
            else:
                fields = await enhance_result(
                    pref_config=pref_config,
                    name=name,
                    result_fields=normalized.fields,
                    ok=ok,
                    reporter=ToolEnhanceReporter(
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
            fields = normalized.fields

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
        hook_response=hook_tool_response(
            name,
            result,
            fields=fields,
            text=normalized.display_text,
            tools=tools,
        ),
        cost_ms=cost_ms,
        status="completed" if ok else "failed",
    )


def hook_tool_response(
    name: str,
    result: typing.Any,
    *,
    fields: dict[str, typing.Any],
    text: str,
    tools: list[dict[str, typing.Any]]
) -> typing.Any:
    """按工具来源构建后置 Hook 使用的稳定响应。"""
    meta = meta_for_tool(tools, name)

    client_builtin = (
        bool(meta.get("client_builtin", False))
        and not bool(meta.get("external", False))
    )

    if client_builtin and name in {
        "shell_command",
        "exec_command",
        "write_stdin",
    }:
        return _shell_hook_response(fields, fallback=text)

    if client_builtin and name == "apply_patch":
        return str(text or "")

    if not client_builtin and isinstance(result, mcp_types.CallToolResult):
        return serialize_call_tool_result(result)

    return dict(fields)


def _shell_hook_response(
    fields: dict[str, typing.Any],
    *,
    fallback: str
) -> str:
    """从原生 Shell 结果中提取受输出上限约束的实际输出。"""
    data = _tool_result_data_map(fields)
    has_output_fields = any(
        key in data
        for key in ("output", "output_lines", "stdout", "stderr")
    )

    output = data.get("output")
    if isinstance(output, str) and output:
        return output

    output_lines = data.get("output_lines")
    if isinstance(output_lines, list):
        combined = "\n".join(
            str(value)
            for value in output_lines
            if str(value)
        )
        if combined:
            return _clip_hook_response(combined, data.get("output_limit"))

    streams = tuple(
        value
        for key in ("stdout", "stderr")
        for value in [data.get(key)]
        if isinstance(value, str) and value
    )
    if streams:
        return _clip_hook_response("\n".join(streams), data.get("output_limit"))

    if has_output_fields:
        return ""
    return str(fallback or "")


def _clip_hook_response(text: str, raw_limit: typing.Any) -> str:
    """按原生执行结果声明的字符上限截断 Hook 输出。"""
    if isinstance(raw_limit, bool) or not isinstance(raw_limit, int):
        return text
    limit = max(1, raw_limit)
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"


if __name__ == '__main__':
    pass
