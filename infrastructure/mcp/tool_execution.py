# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import functools
import time
import typing
from collections.abc import Mapping

from mcp import types as mcp_types

from agent.application.tools.catalog import meta_for_tool
from agent.application.tools.execution import (
    ToolExecutionAdapter,
    ToolExecutionResult,
)
from agent.application.turns.context import ToolInvocation
from agent.application.views.contracts import PresentationSink
from agent.application.views.tool_execution import (
    ToolEnhancementPresenter,
    show_tool_progress,
)
from agent.domain.tool_policy import is_approval_only_tool
from agent.ports import (
    McpSessionPort,
    OutputStatusPort,
)
from infrastructure.mcp.nested_tool_results import nested_tool_output
from infrastructure.mcp.tool_invocation import execute_tool
from infrastructure.mcp.tool_results import (
    normalize_call_tool_result,
    normalize_tool_fields,
    serialize_call_tool_result,
)
from infrastructure.services.tool_result_enhancement import enhance_tool_result
from observability import (
    observe,
    observe_exception,
)


def _tool_result_data(fields: typing.Union[str, dict[str, typing.Any], typing.Any]) -> typing.Any:
    """从工具结果字段中提取结构化数据。"""
    if isinstance(fields, dict):
        return fields.get("data")
    return None


def _tool_result_data_map(fields: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """提取工具结果中的 data 字段。"""
    data = fields.get("data")
    return data if isinstance(data, dict) else {}


def _optional_nested_output(
    session: McpSessionPort,
    *,
    tool_name: str,
    result: mcp_types.CallToolResult,
    call_id: str,
) -> dict[str, typing.Any] | None:
    """为可成功回灌的结果生成嵌套输出，本地失败保留空值。"""
    try:
        return nested_tool_output(
            session,
            tool_name=tool_name,
            result=result,
            call_id=call_id,
        )
    except RuntimeError:
        return None


def server_tool_output_result(
    event: dict[str, typing.Any]
) -> ToolExecutionResult:
    """把服务端回灌的 tool.output 事件转换成展示层结果对象。"""
    raw_fields = _server_output_fields(event)
    reported_ok = _server_output_ok(event, raw_fields)
    status = _server_output_status(event)
    ok = reported_ok if status == "completed" else False
    normalized = normalize_tool_fields(raw_fields, ok=ok)
    fields = normalized.fields
    cost_ms = _server_output_cost_ms(event)

    return ToolExecutionResult(
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
) -> typing.Literal["completed", "failed", "declined", "cancelled"]:
    """读取服务端工具输出的稳定生命周期状态。"""
    status = str(event.get("status") or "").strip().lower()
    if status == "completed":
        return "completed"
    if status == "failed":
        return "failed"
    if status == "declined":
        return "declined"
    if status == "cancelled":
        return "cancelled"
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
    session: McpSessionPort,
    *,
    status_control: OutputStatusPort,
    presentation: PresentationSink,
    tools: list[dict[str, typing.Any]],
    invocation: ToolInvocation,
    pref_config: dict[str, typing.Any],
    enable_progress_notify: bool = False,
    status_text: typing.Optional[str] = None
) -> ToolExecutionResult:
    """统一执行工具、处理状态动画和结果增强。"""
    started_at = time.time()
    name = invocation.name

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
                fields = await enhance_tool_result(
                    pref_config=pref_config,
                    name=name,
                    result_fields=normalized.fields,
                    ok=ok,
                    reporter=ToolEnhancementPresenter(
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

    return ToolExecutionResult(
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
        nested_output=_optional_nested_output(
            session,
            tool_name=name,
            result=result,
            call_id=invocation.call_id,
        ),
    )


async def run_direct_tool_step(
    session: McpSessionPort,
    *,
    tools: list[dict[str, typing.Any]],
    invocation: ToolInvocation,
    pref_config: Mapping[str, typing.Any],
) -> ToolExecutionResult:
    """执行由 Harness 统一管理展示和 Hook 的内部工具步骤。"""
    started_at = time.perf_counter()
    result = await execute_tool(
        session,
        tools=tools,
        invocation=invocation,
        pref_config=pref_config,
    )
    normalized = normalize_call_tool_result(result)
    return ToolExecutionResult(
        ok=normalized.ok,
        fields=normalized.fields,
        text=normalized.display_text,
        data=normalized.data,
        hook_response=hook_tool_response(
            invocation.name,
            result,
            fields=normalized.fields,
            text=normalized.display_text,
            tools=tools,
        ),
        cost_ms=int((time.perf_counter() - started_at) * 1000),
        status="completed" if normalized.ok else "failed",
        nested_output=_optional_nested_output(
            session,
            tool_name=invocation.name,
            result=result,
            call_id=invocation.call_id,
        ),
    )


class McpToolExecutionAdapter(ToolExecutionAdapter):
    """通过 MCP 会话执行工具并向 Harness 返回稳定结果。"""

    async def execute(
        self,
        session: McpSessionPort,
        *,
        status_control: OutputStatusPort,
        presentation: PresentationSink,
        tools: list[dict[str, typing.Any]],
        invocation: ToolInvocation,
        pref_config: Mapping[str, typing.Any],
        enable_progress_notify: bool = False,
        status_text: str | None = None,
    ) -> ToolExecutionResult:
        """执行带展示状态和增强流程的工具调用。"""
        return await run_tool_step(
            session,
            status_control=status_control,
            presentation=presentation,
            tools=tools,
            invocation=invocation,
            pref_config=dict(pref_config),
            enable_progress_notify=enable_progress_notify,
            status_text=status_text,
        )

    async def execute_direct(
        self,
        session: McpSessionPort,
        *,
        tools: list[dict[str, typing.Any]],
        invocation: ToolInvocation,
        pref_config: Mapping[str, typing.Any],
    ) -> ToolExecutionResult:
        """执行不重复管理展示状态的内部计划步骤。"""
        return await run_direct_tool_step(
            session,
            tools=tools,
            invocation=invocation,
            pref_config=pref_config,
        )

    def project_server_output(
        self,
        payload: Mapping[str, typing.Any],
    ) -> ToolExecutionResult:
        """把协议载荷交给 MCP 结果归一化边界。"""
        return server_tool_output_result(dict(payload))


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
