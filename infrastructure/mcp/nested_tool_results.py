# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from collections.abc import (
    Mapping,
)
from datetime import timedelta

from mcp import types as mcp_types
from mcp.shared.session import ProgressFnT

from agent.application.turns.context import TurnContext
from agent.ports.javascript import (
    NestedToolDispatch,
    NestedToolOutput,
)
from agent.ports.mcp_session import McpSessionPort
from agent.protocol.json_value import (
    freeze_json,
    thaw_object,
)
from infrastructure.mcp.composite_session import CompositeToolSession
from infrastructure.mcp.tool_results import normalize_call_tool_result

__all__ = (
    "create_nested_tool_dispatch",
    "nested_tool_output",
)


def create_nested_tool_dispatch(
    *,
    session: McpSessionPort,
    nested_dispatch: NestedToolDispatch | None,
    read_timeout_seconds: timedelta | None,
    progress_callback: ProgressFnT | None,
    turn_context: TurnContext,
    pref_config: Mapping[str, typing.Any],
) -> NestedToolDispatch:
    """建立把 SDK 工具结果转换为 REPL 稳定 JSON 输出的调用边界。"""

    async def dispatch(
        tool_name: str,
        arguments: dict[str, typing.Any],
        call_id: str,
    ) -> NestedToolOutput:
        """执行一次嵌套工具调用并剥离 MCP SDK 对象。"""
        if nested_dispatch is not None:
            return _validated_output(
                await nested_dispatch(tool_name, arguments, call_id)
            )
        result = await session.call_tool(
            tool_name,
            arguments,
            read_timeout_seconds=read_timeout_seconds,
            progress_callback=progress_callback,
            call_id=call_id,
            turn_context=turn_context,
            pref_config=pref_config,
        )
        if not isinstance(result, mcp_types.CallToolResult):
            raise TypeError("nested tool dispatch must return CallToolResult")
        return _nested_tool_response(
            result,
            call_id=call_id,
            mcp_result=_nested_tool_returns_mcp(session, tool_name),
        )

    return dispatch


def nested_tool_output(
    session: McpSessionPort,
    *,
    tool_name: str,
    result: mcp_types.CallToolResult,
    call_id: str,
) -> NestedToolOutput:
    """把一次具体 MCP 调用结果投影为 Harness 可传递的嵌套输出。"""
    if not isinstance(result, mcp_types.CallToolResult):
        raise TypeError("nested tool result must be CallToolResult")
    return _nested_tool_response(
        result,
        call_id=call_id,
        mcp_result=_nested_tool_returns_mcp(session, tool_name),
    )


def _nested_tool_returns_mcp(
    session: McpSessionPort,
    tool_name: str,
) -> bool:
    """判断嵌套结果是否来自 MCP 工具而非本地客户端工具。"""
    if not isinstance(session, CompositeToolSession):
        return True
    if (
        session.client_registry is not None
        and session.client_registry.has_tool(tool_name)
    ):
        return False
    if (
        session.external_group is not None
        and tool_name in session.external_group.tools
    ):
        return True
    return session.service_session is not None


def _nested_tool_response(
    result: mcp_types.CallToolResult,
    *,
    call_id: str,
    mcp_result: bool = False,
) -> NestedToolOutput:
    """把 MCP SDK 工具结果转换为 JavaScript 内核函数输出。"""
    normalized = normalize_call_tool_result(result)
    if not normalized.ok and not mcp_result:
        raise RuntimeError(normalized.display_text)

    if mcp_result:
        output = result.model_dump(
            mode="json",
            by_alias=True,
            exclude_none=True,
        )
        return _validated_output({
            "type": "mcp_tool_call_output",
            "call_id": call_id,
            "output": output,
            "result": (
                {"Ok": output}
                if normalized.ok
                else {"Err": normalized.display_text}
            ),
        })

    content_items: list[dict[str, typing.Any]] = []
    content_has_image = False
    for item in result.content:
        if isinstance(item, mcp_types.TextContent):
            if item.text:
                content_items.append({"type": "input_text", "text": item.text})
            continue
        if isinstance(item, mcp_types.ImageContent):
            content_has_image = True
            detail = None
            meta = item.meta if isinstance(item.meta, dict) else {}
            for key, value in meta.items():
                if str(key).endswith("/imageDetail") and value in {
                    "auto",
                    "low",
                    "high",
                    "original",
                }:
                    detail = value
                    break
            content_items.append({
                "type": "input_image",
                "image_url": f"data:{item.mimeType};base64,{item.data}",
                **({"detail": detail} if detail else {}),
            })

    if content_has_image:
        return _validated_output({
            "type": "function_call_output",
            "call_id": call_id,
            "output": content_items,
        })

    images = [
        item
        for item in normalized.fields.get("attachments", [])
        if isinstance(item, dict)
           and item.get("kind") == "image"
           and str(item.get("data_url") or "").lower().startswith("data:")
    ]
    if images:
        output: typing.Any = [
            {
                "type": "input_image",
                "image_url": str(item["data_url"]),
                **(
                    {"detail": item["detail"]}
                    if item.get("detail") in {
                        "auto",
                        "low",
                        "high",
                        "original",
                    }
                    else {}
                ),
            }
            for item in images
        ]
    else:
        data = normalized.data
        output = (
            data
            if data not in (None, {}, [], "")
            else str(
                normalized.fields.get("text")
                or normalized.display_text
                or ""
            )
        )

    return _validated_output({
        "type": "function_call_output",
        "call_id": call_id,
        "output": output,
    })


def _validated_output(value: dict[str, typing.Any]) -> NestedToolOutput:
    """校验并复制 JavaScript 内核可见的 JSON 对象。"""
    frozen = freeze_json(value, field_name="nested tool output")
    return thaw_object(frozen, field_name="nested tool output")


if __name__ == '__main__':
    pass
