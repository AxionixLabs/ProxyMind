# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from pathlib import Path
from mcp import types as mcp_types
from .types import (
    ClientTool,
    ClientToolRuntime
)
from .coding import coding_tools
from .planning import planning_tools
from .update_plan import update_plan_tools
from .view_image import view_image_tools

if typing.TYPE_CHECKING:
    from mind_core.permissions import PermissionSettings


class ClientToolRegistry:
    """客户端工具的注册表与分发器。"""

    def __init__(self, tools: typing.Iterable[ClientTool] | None = None) -> None:
        self._tools: dict[str, ClientTool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: ClientTool) -> None:
        """按名称注册一个客户端工具。"""
        name = str(tool.name or "").strip()
        if not name:
            raise ValueError("client tool name is required")
        if name in self._tools:
            raise ValueError(f"duplicate client tool: {name}")
        self._tools[name] = tool

    def has_tool(self, name: str) -> bool:
        """判断指定客户端工具是否存在。"""
        return str(name or "").strip() in self._tools

    def list_tools(self) -> mcp_types.ListToolsResult:
        """返回全部客户端工具的 MCP 兼容描述。"""
        return mcp_types.ListToolsResult(
            tools=[tool.to_mcp_tool() for tool in self._tools.values()]
        )

    async def call_tool(
        self,
        session: typing.Any,
        name: str,
        arguments: dict[str, typing.Any] | None = None,
        *,
        read_timeout_seconds: typing.Any = None,
        progress_callback: typing.Any = None,
        meta: dict[str, typing.Any] | None = None,
        execution: dict[str, typing.Any] | None = None,
        cid: str | None = None,
        sid: str | None = None,
        call_id: str | None = None,
        permissions: "PermissionSettings | None" = None
    ) -> mcp_types.CallToolResult:
        """分发一次客户端工具调用。"""
        key  = str(name or "").strip()
        tool = self._tools.get(key)

        if tool is None:
            raise KeyError(f"unknown client tool: {name}")

        runtime = ClientToolRuntime(
            session=session,
            read_timeout_seconds=read_timeout_seconds,
            progress_callback=progress_callback,
            meta=meta,
            execution=execution,
            cid=cid,
            sid=sid,
            call_id=call_id,
            permissions=permissions,
        )
        return await tool.handler(dict(arguments or {}), runtime)


def default_registry(
    native_coding: typing.Any = None,
    *,
    execution_root: str | Path | None = None,
) -> ClientToolRegistry:
    """构建默认客户端工具注册表。"""
    root_source = execution_root
    if root_source is None and native_coding is not None:
        root_source = native_coding.root

    root = Path(root_source or Path.cwd()).resolve()

    return ClientToolRegistry([
        *planning_tools(),
        *update_plan_tools(),
        *coding_tools(native_coding),
        *view_image_tools(root),
    ])


if __name__ == '__main__':
    pass
