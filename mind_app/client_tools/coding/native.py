# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mcp import types as mcp_types
from mind_app.native_coding import NativeCoding
from mind_app.client_tools.types import (
    ClientTool,
    ClientToolRuntime
)
from .schemas import (
    APPLY_PATCH_INPUT_SCHEMA,
    EXEC_COMMAND_INPUT_SCHEMA,
    SHELL_CALLS_INPUT_SCHEMA,
    SHELL_COMMAND_INPUT_SCHEMA,
    WRITE_STDIN_INPUT_SCHEMA,
    exec_command_payload,
    shell_command_items_payload,
    shell_command_payload,
    write_stdin_payload
)


def build_coding_result(
    *,
    tool: str,
    args: dict[str, typing.Any],
    raw: dict[str, typing.Any],
    target: str
) -> mcp_types.CallToolResult:
    """构造编码工具调用结果。"""
    output = dict(raw or {})
    ok     = bool(output.get("ok"))

    data = output.get("data")
    if not isinstance(data, dict):
        data = {}

    text        = str(output.get("text") or data or "")
    result_text = f"tool={tool} target={target} ok={ok} {text}"

    structured: dict[str, typing.Any] | None = {
        "ok": ok,
        "tool": tool,
        "args": dict(args or {}),
        "text": result_text,
        "attachments": list(output.get("attachments") or []),
        "data": data,
        "target": target,
    }

    return mcp_types.CallToolResult(
        content=[mcp_types.TextContent(type="text", text=result_text)],
        structuredContent=structured,
        isError=not ok,
        _meta={"logs": list(output.get("logs") or [])}
    )


def coding_tools(native_coding: NativeCoding | None = None) -> list[ClientTool]:
    """返回编码工具列表。"""
    coding = native_coding or NativeCoding()

    async def shell_command_handler(
        arguments: dict[str, typing.Any],
        runtime: ClientToolRuntime
    ) -> mcp_types.CallToolResult:
        """执行单条命令。"""
        _ = runtime

        args = {
            **shell_command_payload(
                command=arguments.get("command"),
                cwd=arguments.get("cwd", "."),
                timeout_sec=arguments.get("timeout_sec", 60),
                output_encoding=arguments.get("output_encoding", "auto"),
            ),
            "execution": arguments.get("execution"),
        }

        raw = await coding.shell_command(**args)

        return build_coding_result(
            tool="shell_command",
            args=args,
            raw=raw,
            target=coding.agent_id
        )

    async def shell_calls_handler(
        arguments: dict[str, typing.Any],
        runtime: ClientToolRuntime
    ) -> mcp_types.CallToolResult:
        """执行批量命令。"""
        _ = runtime

        args = {
            "items": shell_command_items_payload(arguments.get("items")),
            "execution": arguments.get("execution"),
        }

        raw = await coding.shell_calls(**args)

        return build_coding_result(
            tool="shell_calls",
            args=args,
            raw=raw,
            target=coding.agent_id
        )

    async def apply_patch_handler(
        arguments: dict[str, typing.Any],
        runtime: ClientToolRuntime
    ) -> mcp_types.CallToolResult:
        """应用补丁。"""
        _ = runtime

        args = {
            "patch": str(arguments.get("patch") or ""),
            "expected_sha256": arguments.get("expected_sha256"),
            "force": bool(arguments.get("force", False)),
        }

        raw  = coding.apply_patch(**args)
        data = raw.get("data") if isinstance(raw.get("data"), dict) else {}

        if "delta" in data:
            coding.track_patch_delta(data.get("delta"))

        return build_coding_result(
            tool="apply_patch",
            args=args,
            raw=raw,
            target=coding.agent_id
        )

    async def exec_command_handler(
        arguments: dict[str, typing.Any],
        runtime: ClientToolRuntime
    ) -> mcp_types.CallToolResult:
        """启动可持续命令会话。"""
        _ = runtime

        args = {
            **exec_command_payload(
                command=arguments.get("command"),
                cwd=arguments.get("cwd", "."),
                yield_time_ms=arguments.get("yield_time_ms", 1000),
                max_output_chars=arguments.get("max_output_chars", 24000),
                timeout_sec=arguments.get("timeout_sec", 1800),
                idle_timeout_sec=arguments.get("idle_timeout_sec", 300),
            ),
            "execution": arguments.get("execution"),
        }

        raw = await coding.exec_command(**args)

        return build_coding_result(
            tool="exec_command",
            args=args,
            raw=raw,
            target=coding.agent_id
        )

    async def write_stdin_handler(
        arguments: dict[str, typing.Any],
        runtime: ClientToolRuntime
    ) -> mcp_types.CallToolResult:
        """写入或轮询命令会话。"""
        _ = runtime

        args = write_stdin_payload(
            session_id=arguments.get("session_id"),
            stdin=arguments.get("stdin", ""),
            wait_ms=arguments.get("wait_ms", 1000),
            max_output_chars=arguments.get("max_output_chars", 12000),
            control=arguments.get("control", "none"),
        )

        raw = await coding.write_stdin(**args)

        return build_coding_result(
            tool="write_stdin",
            args=args,
            raw=raw,
            target=coding.agent_id
        )

    return [
        ClientTool(
            name="shell_command",
            description=(
                "在工作区内执行单条本地 shell 命令。命令由系统默认 shell 解释执行，"
                "适合运行单个诊断命令、测试、构建或脚本。代码修改请使用 apply_patch。"
            ),
            input_schema=SHELL_COMMAND_INPUT_SCHEMA,
            meta={"hidden": False, "domain": "coding", "class": "shell"},
            handler=shell_command_handler,
        ),
        ClientTool(
            name="shell_calls",
            description=(
                "在工作区内批量执行本地 shell 命令。每项包含 command，可包含 cwd 和 timeout_sec。"
                "代码修改请使用 apply_patch。"
            ),
            input_schema=SHELL_CALLS_INPUT_SCHEMA,
            meta={"hidden": False, "domain": "coding", "class": "shell"},
            handler=shell_calls_handler,
        ),
        ClientTool(
            name="exec_command",
            description=(
                "启动可持续读写的本地 shell 命令会话。适合长耗时任务、交互式任务和持续输出。"
                "该实现使用标准输入输出管道，不提供真实 PTY。"
            ),
            input_schema=EXEC_COMMAND_INPUT_SCHEMA,
            meta={"hidden": False, "domain": "coding", "class": "shell"},
            handler=exec_command_handler,
        ),
        ClientTool(
            name="write_stdin",
            description=(
                "向 exec_command 创建的命令会话写入标准输入，或在 stdin 为空时轮询增量输出。"
            ),
            input_schema=WRITE_STDIN_INPUT_SCHEMA,
            meta={"hidden": False, "domain": "coding", "class": "shell"},
            handler=write_stdin_handler,
        ),
        ClientTool(
            name="apply_patch",
            description=(
                "应用严格 apply_patch 补丁修改工作区文件。支持多文件、新建、更新、删除和上下文校验。"
            ),
            input_schema=APPLY_PATCH_INPUT_SCHEMA,
            meta={"hidden": False, "domain": "coding", "class": "workspace"},
            handler=apply_patch_handler,
        ),
    ]


if __name__ == '__main__':
    pass
