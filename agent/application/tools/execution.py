# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from collections.abc import (
    Mapping,
    Sequence,
)
from dataclasses import dataclass

from agent.application.turns.context import ToolInvocation
from agent.application.views.contracts import PresentationSink
from agent.ports.javascript import NestedToolOutput
from agent.ports.mcp_session import McpSessionPort

__all__ = (
    "ClientToolResultEnvelope",
    "ToolExecutionAdapter",
    "ToolExecutionResult",
    "ToolLifecycleStatus",
    "build_client_tool_result",
)


ToolLifecycleStatus: typing.TypeAlias = typing.Literal[
    "completed",
    "failed",
    "declined",
    "cancelled",
]

_REMOVED_RESULT_FIELDS = frozenset({
    "pending_cloud_sandbox",
    "sandbox_requests",
    "cloud_schema",
})


class ClientToolResultEnvelope(typing.TypedDict):
    """描述 Harness 向协议端口提交的稳定客户端工具结果。"""

    ok: bool
    tool: str
    source: str
    args: dict[str, typing.Any]
    text: str
    attachments: list[typing.Any]
    data: dict[str, typing.Any]


@dataclass(slots=True)
class ToolExecutionResult:
    """隔离具体工具 SDK 后的一次执行结果。"""

    ok: bool
    fields: dict[str, typing.Any]
    text: str
    data: typing.Any
    hook_response: typing.Any
    cost_ms: int
    status: ToolLifecycleStatus
    nested_output: NestedToolOutput | None = None


@typing.runtime_checkable
class ToolExecutionAdapter(typing.Protocol):
    """定义 Harness 调用具体工具基础设施的单次执行边界。

    实现方负责 SDK 调用、结果归一化和进度适配，但不得持有 Turn、Hook 或效果账本
    生命周期；每次调用必须返回不包含具体 SDK 对象的稳定结果。
    """

    async def execute(
        self,
        session: McpSessionPort,
        *,
        presentation: PresentationSink,
        tools: list[dict[str, typing.Any]],
        invocation: ToolInvocation,
        pref_config: Mapping[str, typing.Any],
        enable_progress_notify: bool = False,
    ) -> ToolExecutionResult:
        """执行带结构化进度和结果增强的普通工具调用。"""
        ...

    async def execute_direct(
        self,
        session: McpSessionPort,
        *,
        tools: list[dict[str, typing.Any]],
        invocation: ToolInvocation,
        pref_config: Mapping[str, typing.Any],
    ) -> ToolExecutionResult:
        """执行由上层批次生命周期统一展示的内部步骤。"""
        ...

    def project_server_output(
        self,
        payload: Mapping[str, typing.Any],
    ) -> ToolExecutionResult:
        """把服务端托管工具输出投影为同一稳定执行结果。"""
        ...


def build_client_tool_result(
    *,
    tool: str,
    ok: bool,
    args: Mapping[str, typing.Any],
    text: str,
    attachments: Sequence[typing.Any] = (),
    data: Mapping[str, typing.Any] | None = None,
) -> ClientToolResultEnvelope:
    """构建不携带历史兼容字段的客户端工具结果。"""
    normalized_tool = str(tool or "").strip()
    if not normalized_tool:
        raise ValueError("tool result tool must be a non-empty string")
    if not isinstance(ok, bool):
        raise TypeError("tool result ok must be a boolean")
    if not isinstance(args, Mapping):
        raise TypeError("tool result args must be an object")
    if not isinstance(text, str):
        raise TypeError("tool result text must be a string")
    if not isinstance(attachments, (list, tuple)):
        raise TypeError("tool result attachments must be a list")
    result_data = {} if data is None else data
    if not isinstance(result_data, Mapping):
        raise TypeError("tool result data must be an object")
    removed = sorted(_REMOVED_RESULT_FIELDS.intersection(result_data))
    if removed:
        raise ValueError(
            "tool result contains removed cloud sandbox handoff fields: "
            + ", ".join(removed)
        )
    return ClientToolResultEnvelope(
        ok=ok,
        tool=normalized_tool,
        source="client",
        args=dict(args),
        text=text,
        attachments=list(attachments),
        data=dict(result_data),
    )


if __name__ == '__main__':
    pass
