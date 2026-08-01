# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import typing
from mcp import types as mcp_types
from mind_app.client_tools.result import client_tool_result
from mind_app.client_tools.types import (
    ClientTool,
    ClientToolRuntime
)
from mind_app.runtime.subagents.control import (
    AgentSnapshot,
    AgentStateError
)
from mind_app.runtime.subagents.context import normalize_fork_turns
from mind_app.runtime.subagents.runtime import SubagentRuntime
from mind_app.runtime.execution import AgentContext

SPAWN_AGENT_TOOL  = "spawn_agent"
SEND_INPUT_TOOL   = "send_input"
RESUME_AGENT_TOOL = "resume_agent"
WAIT_AGENT_TOOL   = "wait_agent"
CLOSE_AGENT_TOOL  = "close_agent"

DEFAULT_WAIT_TIMEOUT_MS = 30_000
MIN_WAIT_TIMEOUT_MS     = 10_000
MAX_WAIT_TIMEOUT_MS     = 3_600_000


def subagent_tools(agents: SubagentRuntime) -> list[ClientTool]:
    """返回本地多执行主体控制工具。"""
    return [
        ClientTool(
            name=SPAWN_AGENT_TOOL,
            description=(
                "Spawn a sub-agent for a well-scoped task. The new agent inherits "
                "the current turn's runtime configuration and permissions. Use only "
                "when the user or project instructions authorize delegation."
            ),
            input_schema=_spawn_schema(),
            handler=_spawn_handler(agents),
            meta=_tool_meta("spawn"),
        ),
        ClientTool(
            name=SEND_INPUT_TOOL,
            description=(
                "Send a message to an existing agent. Set interrupt=true to stop "
                "the current turn and handle this input next; otherwise it is queued."
            ),
            input_schema=_send_schema(),
            handler=_send_handler(agents),
            meta=_tool_meta("send"),
        ),
        ClientTool(
            name=RESUME_AGENT_TOOL,
            description=(
                "Resume a previously closed agent so it can receive send_input and "
                "wait_agent calls."
            ),
            input_schema=_resume_schema(),
            handler=_resume_handler(agents),
            meta=_tool_meta("resume"),
        ),
        ClientTool(
            name=WAIT_AGENT_TOOL,
            description=(
                "Wait for any target agent to reach a final status. Completed "
                "statuses may include the agent's final message."
            ),
            input_schema=_wait_schema(),
            handler=_wait_handler(agents),
            meta=_tool_meta("wait"),
        ),
        ClientTool(
            name=CLOSE_AGENT_TOOL,
            description=(
                "Close an agent and its open descendants when no longer needed. "
                "Completed agents keep a concurrency slot until closed."
            ),
            input_schema=_close_schema(),
            handler=_close_handler(agents),
            meta=_tool_meta("close"),
        ),
    ]


def _spawn_handler(agents: SubagentRuntime):
    """创建新执行主体工具处理函数。"""
    async def handle(
        arguments: dict[str, typing.Any],
        tool_runtime: ClientToolRuntime
    ) -> mcp_types.CallToolResult:
        try:
            message    = _required_text(arguments, "message")
            agent_type = _spawn_agent_type(arguments, tool_runtime)
            task_name  = _required_text(arguments, "task_name")
            fork_turns = normalize_fork_turns(arguments.get("fork_turns"))

            snapshot = await agents.spawn(
                tool_runtime.turn_context,
                message,
                tool_runtime.pref_config,
                agent_type=agent_type,
                task_name=task_name,
                fork_turns=fork_turns,
            )

            return client_tool_result(
                tool=SPAWN_AGENT_TOOL,
                ok=True,
                text=f"spawned agent {snapshot.context.task_path}",
                args=arguments,
                data={
                    "agent_id": snapshot.agent_id,
                    "task_name": snapshot.context.task_name,
                    "task_path": snapshot.context.task_path,
                    "fork_turns": snapshot.thread.fork_turns,
                },
            )
        except asyncio.CancelledError:
            raise
        except (TypeError, ValueError, RuntimeError) as error:
            return _error_result(SPAWN_AGENT_TOOL, arguments, error)

    return handle


def _send_handler(agents: SubagentRuntime):
    """创建已有执行主体输入工具处理函数。"""
    async def handle(
        arguments: dict[str, typing.Any],
        tool_runtime: ClientToolRuntime
    ) -> mcp_types.CallToolResult:
        try:
            target    = _required_text(arguments, "target")
            message   = _required_text(arguments, "message")
            interrupt = _optional_bool(arguments, "interrupt", default=False)
            caller    = tool_runtime.turn_context.agent

            if interrupt and target == caller.agent_id:
                raise AgentStateError("an agent cannot interrupt its own active turn")

            submission_id = await agents.submit(
                caller.root_session_id,
                target,
                message,
                interrupt=interrupt,
            )

            return client_tool_result(
                tool=SEND_INPUT_TOOL,
                ok=True,
                text=f"submitted input {submission_id}",
                args=arguments,
                data={"submission_id": submission_id},
            )
        except asyncio.CancelledError:
            raise
        except (TypeError, ValueError, RuntimeError) as error:
            return _error_result(SEND_INPUT_TOOL, arguments, error)

    return handle


def _resume_handler(agents: SubagentRuntime):
    """创建执行主体恢复工具处理函数。"""
    async def handle(
        arguments: dict[str, typing.Any],
        tool_runtime: ClientToolRuntime
    ) -> mcp_types.CallToolResult:
        try:
            agent_id        = _required_text(arguments, "id")
            root_session_id = tool_runtime.turn_context.agent.root_session_id
            snapshot        = await agents.resume(root_session_id, agent_id)

            return client_tool_result(
                tool=RESUME_AGENT_TOOL,
                ok=True,
                text=f"agent {agent_id} status={snapshot.status}",
                args=arguments,
                data={"status": _agent_status(snapshot)},
            )
        except asyncio.CancelledError:
            raise
        except (TypeError, ValueError, RuntimeError) as error:
            return _error_result(RESUME_AGENT_TOOL, arguments, error)

    return handle


def _wait_handler(agents: SubagentRuntime):
    """创建执行主体等待工具处理函数。"""
    async def handle(
        arguments: dict[str, typing.Any],
        tool_runtime: ClientToolRuntime
    ) -> mcp_types.CallToolResult:
        try:
            targets = _targets(arguments.get("targets"))
            caller  = tool_runtime.turn_context.agent

            if caller.agent_id in targets:
                raise AgentStateError("an agent cannot wait for its own active turn")

            timeout_ms = _wait_timeout_ms(arguments.get("timeout_ms"))

            waited = await agents.wait(
                caller.root_session_id,
                targets,
                timeout_sec=timeout_ms / 1000,
            )

            statuses = {
                snapshot.agent_id: _agent_status(snapshot)
                for snapshot in waited.snapshots
            }

            return client_tool_result(
                tool=WAIT_AGENT_TOOL,
                ok=True,
                text=(
                    "agent wait timed out"
                    if waited.timed_out
                    else f"agent wait completed count={len(statuses)}"
                ),
                args=arguments,
                data={"status": statuses, "timed_out": waited.timed_out},
            )
        except asyncio.CancelledError:
            raise
        except (TypeError, ValueError, RuntimeError) as error:
            return _error_result(WAIT_AGENT_TOOL, arguments, error)

    return handle


def _close_handler(agents: SubagentRuntime):
    """创建执行主体关闭工具处理函数。"""
    async def handle(
        arguments: dict[str, typing.Any],
        tool_runtime: ClientToolRuntime
    ) -> mcp_types.CallToolResult:
        try:
            target = _required_text(arguments, "target")
            caller = tool_runtime.turn_context.agent

            await _reject_closing_caller_tree(agents, caller, target)

            previous = await agents.close(caller.root_session_id, target)

            return client_tool_result(
                tool=CLOSE_AGENT_TOOL,
                ok=True,
                text=f"closed agent {target}",
                args=arguments,
                data={"previous_status": _agent_status(previous)},
            )
        except asyncio.CancelledError:
            raise
        except (TypeError, ValueError, RuntimeError) as error:
            return _error_result(CLOSE_AGENT_TOOL, arguments, error)

    return handle


async def _reject_closing_caller_tree(
    agents: SubagentRuntime,
    caller: AgentContext,
    target: str
) -> None:
    """拒绝会把当前调用轮次一并关闭的目标。"""
    if caller.depth == 0:
        return None

    snapshots = await agents.snapshots(caller.root_session_id)

    parents = {
        snapshot.agent_id: snapshot.context.parent_agent_id
        for snapshot in snapshots
    }

    current: str | None = caller.agent_id
    while current and current != "root":
        if current == target:
            raise AgentStateError("an agent cannot close itself or an ancestor")
        current = parents.get(current)


def _agent_status(snapshot: AgentSnapshot) -> typing.Any:
    """转换执行主体快照为稳定工具状态。"""
    if snapshot.status == "completed":
        final_message = getattr(snapshot.result, "assistant_text", None)
        return {"completed": final_message or None}
    if snapshot.status == "failed":
        return {"errored": snapshot.error or "agent execution failed"}
    if snapshot.status == "pending":
        return "pending_init"
    if snapshot.status == "closed":
        return "shutdown"

    return snapshot.status


def _spawn_agent_type(
    arguments: dict[str, typing.Any],
    tool_runtime: ClientToolRuntime
) -> str:
    """返回显式或继承的执行主体类型。"""
    value = arguments.get("agent_type")
    if value is not None and not isinstance(value, str):
        raise TypeError("agent_type must be a string")

    normalized = str(value or "").strip()
    if normalized:
        return normalized

    parent = tool_runtime.turn_context.agent
    return parent.agent_type if parent.depth > 0 else "default"


def _required_text(arguments: dict[str, typing.Any], field: str) -> str:
    """读取必需的非空文本参数。"""
    value = arguments.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _optional_bool(
    arguments: dict[str, typing.Any],
    field: str,
    *,
    default: bool
) -> bool:
    """读取可选布尔参数。"""
    value = arguments.get(field, default)
    if not isinstance(value, bool):
        raise TypeError(f"{field} must be a boolean")
    return value


def _targets(value: typing.Any) -> tuple[str, ...]:
    """校验并去重等待目标。"""
    if not isinstance(value, list) or not value:
        raise ValueError("targets must be a non-empty array")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError("targets must contain non-empty strings")

    return tuple(dict.fromkeys(item.strip() for item in value))


def _wait_timeout_ms(value: typing.Any) -> int:
    """校验并限制等待超时。"""
    if value is None:
        return DEFAULT_WAIT_TIMEOUT_MS
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("timeout_ms must be a number")
    if value <= 0:
        raise ValueError("timeout_ms must be greater than zero")

    return max(MIN_WAIT_TIMEOUT_MS, min(MAX_WAIT_TIMEOUT_MS, int(value)))


def _error_result(
    tool: str,
    arguments: dict[str, typing.Any],
    error: Exception
) -> mcp_types.CallToolResult:
    """把可预期的控制错误转换为工具失败结果。"""
    text = f"{type(error).__name__}: {error}"

    return client_tool_result(
        tool=tool,
        ok=False,
        text=text,
        args=arguments,
        data={"error": text},
    )


def _tool_meta(tool_class: str) -> dict[str, typing.Any]:
    """返回多执行主体工具的通用元数据。"""
    return {
        "hidden" : False,
        "domain" : "client",
        "class"  : tool_class
    }


def _spawn_schema() -> dict[str, typing.Any]:
    """返回创建工具输入结构。"""
    return {
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "Initial task."},
            "task_name": {
                "type": "string",
                "description": "Stable lowercase task name.",
                "pattern": "^[a-z0-9_]+$",
            },
            "agent_type": {
                "type": "string",
                "description": "Optional agent type override.",
            },
            "fork_turns": {
                "type": "string",
                "description": "Parent context: none, all, or a positive turn count.",
                "pattern": "^(none|all|[1-9][0-9]*)$",
                "default": "all",
            },
        },
        "required": ["message", "task_name"],
        "additionalProperties": False,
    }


def _send_schema() -> dict[str, typing.Any]:
    """返回输入投递工具结构。"""
    return {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Agent id."},
            "message": {"type": "string", "description": "Message text."},
            "interrupt": {
                "type": "boolean",
                "description": "Interrupt the active turn before this input.",
            },
        },
        "required": ["target", "message"],
        "additionalProperties": False,
    }


def _resume_schema() -> dict[str, typing.Any]:
    """返回恢复工具输入结构。"""
    return {
        "type": "object",
        "properties": {"id": {"type": "string", "description": "Agent id."}},
        "required": ["id"],
        "additionalProperties": False,
    }


def _wait_schema() -> dict[str, typing.Any]:
    """返回等待工具输入结构。"""
    return {
        "type": "object",
        "properties": {
            "targets": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "description": "Agent ids; returns when any reaches a final status.",
            },
            "timeout_ms": {
                "type": "number",
                "description": (
                    f"Timeout in milliseconds. Defaults to {DEFAULT_WAIT_TIMEOUT_MS}, "
                    f"min {MIN_WAIT_TIMEOUT_MS}, max {MAX_WAIT_TIMEOUT_MS}."
                ),
            },
        },
        "required": ["targets"],
        "additionalProperties": False,
    }


def _close_schema() -> dict[str, typing.Any]:
    """返回关闭工具输入结构。"""
    return {
        "type": "object",
        "properties": {"target": {"type": "string", "description": "Agent id."}},
        "required": ["target"],
        "additionalProperties": False,
    }


if __name__ == '__main__':
    pass
