# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import json
import re
import typing
from dataclasses import dataclass
from collections.abc import Callable

from agent.domain.hooks import (
    HookDefinitionConfig,
    McpToolHookHandlerConfig,
)
from agent.ports import ExternalToolGroupPort

_PLACEHOLDER = re.compile(r"\$\{([^{}]+)\}")


class HookMcpError(RuntimeError):
    """表示 MCP Hook 调用或返回值不符合 Hook 契约。"""


@dataclass(frozen=True, slots=True)
class HookMcpOutput:
    """保存 MCP Hook 返回的结构化输出。"""

    data: dict[str, typing.Any]
    stderr: str = ""


class HookMcpRunner:
    """通过既有外部 MCP 工具组执行 Hook 调用。"""

    def __init__(
        self,
        group_provider: Callable[[], ExternalToolGroupPort | None],
    ) -> None:
        """绑定当前 ExecutionResources 提供的 MCP 工具组。"""
        if not callable(group_provider):
            raise TypeError("MCP Hook group provider must be callable")
        self._group_provider = group_provider

    async def execute(
        self,
        definition: HookDefinitionConfig,
        payload: dict[str, typing.Any],
    ) -> HookMcpOutput:
        """展开静态参数并调用指定 MCP 工具。"""
        handler = definition.handler
        if not isinstance(handler, McpToolHookHandlerConfig):
            raise TypeError("MCP Hook runner requires an mcp_tool handler")

        group = self._group_provider()
        if group is None:
            raise HookMcpError("MCP runtime is not started")

        arguments = _expand_value(handler.input, payload)
        if not isinstance(arguments, dict):
            raise HookMcpError("MCP Hook input must be an object")

        try:
            result = await asyncio.wait_for(
                group.call_hook_tool(
                    handler.server,
                    handler.tool,
                    arguments,
                    read_timeout_seconds=None,
                ),
                timeout=handler.timeout_sec,
            )
        except asyncio.TimeoutError as error:
            raise HookMcpError(
                f"MCP Hook timed out after {handler.timeout_sec:g}s"
            ) from error
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise HookMcpError(str(error) or type(error).__name__) from error

        if result.isError:
            raise HookMcpError(_result_text(result) or "MCP Hook returned an error")

        return HookMcpOutput(data=_result_data(result))


def _expand_value(value: typing.Any, payload: dict[str, typing.Any]) -> typing.Any:
    """递归展开 `${field.path}` 参数模板。"""
    if isinstance(value, dict):
        return {
            str(key): _expand_value(item, payload)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_expand_value(item, payload) for item in value]
    if not isinstance(value, str):
        return value

    matches = tuple(_PLACEHOLDER.finditer(value))
    if not matches:
        return value
    if len(matches) == 1 and matches[0].span() == (0, len(value)):
        return _lookup(payload, matches[0].group(1))

    pieces: list[str] = []
    end = 0
    for match in matches:
        pieces.append(value[end:match.start()])
        replacement = _lookup(payload, match.group(1))
        pieces.append(
            replacement
            if isinstance(replacement, str)
            else json.dumps(
                replacement,
                ensure_ascii=True,
                separators=(",", ":"),
            )
        )
        end = match.end()
    pieces.append(value[end:])
    return "".join(pieces)


def _lookup(payload: dict[str, typing.Any], path: str) -> typing.Any:
    """读取参数模板引用的事件字段。"""
    current: typing.Any = payload
    for segment in path.split("."):
        if not isinstance(current, dict) or segment not in current:
            raise HookMcpError(f"MCP Hook input field is missing: {path}")
        current = current[segment]
    return current


def _result_data(result: typing.Any) -> dict[str, typing.Any]:
    """把 MCP 结构化或文本 JSON 结果转换为 Hook 对象。"""
    structured = result.structuredContent
    if isinstance(structured, dict):
        return dict(structured)

    text = _result_text(result)
    if not text:
        return {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise HookMcpError("MCP Hook returned non-JSON output") from error
    if not isinstance(value, dict):
        raise HookMcpError("MCP Hook output must be a JSON object")
    return value


def _result_text(result: typing.Any) -> str:
    """提取 MCP 文本内容用于错误或 JSON 回退解析。"""
    parts: list[str] = []
    for item in result.content:
        text = getattr(item, "text", None)
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())
    return "\n".join(parts)


if __name__ == '__main__':
    pass
