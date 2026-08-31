# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import copy
import typing
from pathlib import Path
from mcp import types as mcp_types
from agent.application import FeatureSettings
from agent.application.execution import TurnContext
from .types import (
    ClientTool,
    ClientToolRuntime,
    NESTED_TOOL_DISPATCH_META_KEY,
    TURN_INTERRUPT_META_KEY,
)
from .coding import coding_tools
from .planning import planning_tools
from .subagents import subagent_tools
from .update_plan import update_plan_tools
from .view_image import view_image_tools

if typing.TYPE_CHECKING:
    from mind_app.approval.coordinator import ApprovalCoordinator
    from mind_app.native_coding.exec.exec_policy import ExecPolicyManager
    from mind_app.runtime.subagents.runtime import SubagentRuntime

JS_REPL_TOOL_NAMES = frozenset({"js_repl", "js_repl_reset"})


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
        call_id: str | None = None,
        turn_context: TurnContext | None = None,
        pref_config: typing.Mapping[str, typing.Any] | None = None
    ) -> mcp_types.CallToolResult:
        """分发一次客户端工具调用。"""
        key  = str(name or "").strip()
        tool = self._tools.get(key)

        if tool is None:
            raise KeyError(f"unknown client tool: {name}")
        if not isinstance(turn_context, TurnContext):
            raise TypeError("client tool turn context is required")
        if not isinstance(pref_config, typing.Mapping):
            raise TypeError("client tool preference config is required")

        runtime_meta = dict(meta or {})

        nested_dispatch = runtime_meta.pop(
            NESTED_TOOL_DISPATCH_META_KEY,
            None,
        )
        turn_interrupt = runtime_meta.pop(
            TURN_INTERRUPT_META_KEY,
            None,
        )

        runtime = ClientToolRuntime(
            session=session,
            turn_context=turn_context,
            pref_config=copy.deepcopy(dict(pref_config)),
            read_timeout_seconds=read_timeout_seconds,
            progress_callback=progress_callback,
            meta=runtime_meta or None,
            call_id=call_id,
            nested_tool_dispatch=(
                nested_dispatch if callable(nested_dispatch) else None
            ),
            interrupt_turn=(
                turn_interrupt if callable(turn_interrupt) else None
            ),
        )
        return await tool.handler(dict(arguments or {}), runtime)


def default_registry(
    native_coding: typing.Any = None,
    *,
    execution_root: str | Path | None = None,
    exec_policy_manager: "ExecPolicyManager | None" = None,
    subagent_runtime: "SubagentRuntime | None" = None,
    approval_coordinator: "ApprovalCoordinator | None" = None,
    features: FeatureSettings | None = None,
) -> ClientToolRegistry:
    """构建默认客户端工具注册表。"""
    feature_settings = features or FeatureSettings()
    root_source = execution_root
    if root_source is None and native_coding is not None:
        root_source = native_coding.root

    root = Path(root_source or Path.cwd()).resolve()

    coding = coding_tools(
        native_coding,
        approval_coordinator=approval_coordinator,
        exec_policy_manager=exec_policy_manager,
        exec_permission_approvals_enabled=feature_settings.exec_permission_approvals,
    )
    if not feature_settings.js_repl:
        coding = [
            tool
            for tool in coding
            if tool.name not in JS_REPL_TOOL_NAMES
        ]

    tools = [
        *planning_tools(),
        *update_plan_tools(),
        *coding,
        *view_image_tools(root),
    ]

    if subagent_runtime is not None and subagent_runtime.enabled:
        tools.extend(subagent_tools(subagent_runtime))

    return ClientToolRegistry(tools)


if __name__ == '__main__':
    pass
