# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
import functools
from dataclasses import dataclass
from mind_app.mcp import McpSessionLike
from mind_app.presentation.contracts import PresentationSink
from engine.enhance import enhance_result
from ...output import OutputControlPort
from .enhance_reporter import ToolEnhanceReporter
from .progress import show_tool_progress
from .router import execute_tool

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
    fields: typing.Union[str, dict[str, typing.Any]]
    text: str
    data: typing.Any
    cost_ms: int


def _tool_result_text(fields: typing.Union[str, dict[str, typing.Any], typing.Any]) -> str:
    """从工具结果字段中提取文本摘要。"""
    if isinstance(fields, dict):
        value = fields.get("text")
        return "" if value is None else str(value)
    if fields is None:
        return ""
    return str(fields)


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
    fields: typing.Union[str, dict[str, typing.Any]]
) -> typing.Union[str, dict[str, typing.Any]]:
    """为 synthetic 原生工具调用补充稳定的顶层结果字段。"""
    if not isinstance(fields, dict):
        return fields

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
    fields = _server_output_fields(event)
    fields = normalize_tool_result_fields(name, fields)

    ok      = _server_output_ok(event, fields)
    cost_ms = _server_output_cost_ms(event)

    return ToolRunResult(
        result=fields,
        ok=ok,
        fields=fields,
        text=_tool_result_text(fields),
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
    stream_ui: OutputControlPort,
    presentation: PresentationSink,
    tools: list[dict[str, typing.Any]],
    name: str,
    arguments: dict[str, typing.Any],
    meta: typing.Optional[dict[str, typing.Any]],
    pref_config: dict[str, typing.Any],
    enable_progress_notify: bool = False,
    status_text: typing.Optional[str] = None,
    execution: dict[str, typing.Any] | None = None,
    cid: str | None = None,
    sid: str | None = None,
    call_id: str | None = None,
) -> ToolRunResult:
    """统一执行工具、处理状态动画和结果增强。"""
    started_at = time.time()

    if status_text:
        await stream_ui.begin_custom_tool_status(status_text)
    else:
        await stream_ui.begin_tool_status()
    try:
        result = await execute_tool(
            session,
            tools=tools,
            name=name,
            arguments=arguments,
            meta=meta,
            enable_progress_notify=enable_progress_notify,
            stream_callback=functools.partial(
                show_tool_progress,
                presentation,
                source="tool",
                tool_name=name,
            ),
            execution=execution,
            cid=cid,
            sid=sid,
            call_id=call_id,
        )
        ok = not result.isError

        fields = await enhance_result(
            pref_config=pref_config,
            name=name,
            result=result,
            ok=ok,
            reporter=ToolEnhanceReporter(
                stream_ui,
                presentation,
                tool_name=name,
            )
        )
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
