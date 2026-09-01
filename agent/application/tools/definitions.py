# -*- coding: utf-8 -*-
# Notes: ==== Mind(TM) ====

import typing
from collections.abc import (
    Awaitable,
    Callable,
    Mapping,
)
from dataclasses import (
    dataclass,
    field,
)
from agent.application.tools.context import ToolHandlerContext

if typing.TYPE_CHECKING:
    from mcp import types as mcp_types

ToolHandler: typing.TypeAlias = Callable[
    [dict[str, typing.Any], ToolHandlerContext],
    Awaitable["mcp_types.CallToolResult"],
]


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """定义本地工具的稳定描述与调用入口，不包含具体传输对象。"""

    name: str
    description: str
    input_schema: Mapping[str, typing.Any]
    handler: ToolHandler
    meta: Mapping[str, typing.Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ClientTool(ToolDefinition):
    """定义由客户端工作区执行的本地工具。"""


@dataclass(frozen=True, slots=True)
class BuiltinTool(ToolDefinition):
    """定义由 Harness 提供且独立于工作区的本地工具。"""


LocalToolDefinition: typing.TypeAlias = ClientTool | BuiltinTool

__all__ = (
    "BuiltinTool",
    "ClientTool",
    "LocalToolDefinition",
    "ToolDefinition",
    "ToolHandler",
)
