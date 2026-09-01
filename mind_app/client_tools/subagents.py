# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import typing
from agent.application.tools.context import ToolHandlerContext
from agent.application.tools.definitions import ClientTool
from agent.application.tools.results import (
    LocalToolResult,
    client_tool_result,
)
from agent.application.agents.views import AgentSnapshot
from agent.application.agents.fork_context import normalize_fork_turns
from agent.stores.agents.mailbox import MAX_AGENT_MESSAGE_CHARS
from agent.harness.agents.runtime import SubagentRuntime

SPAWN_AGENT_TOOL     = "spawn_agent"
LIST_AGENTS_TOOL     = "list_agents"
SEND_MESSAGE_TOOL    = "send_message"
FOLLOWUP_TASK_TOOL   = "followup_task"
INTERRUPT_AGENT_TOOL = "interrupt_agent"
RESUME_AGENT_TOOL    = "resume_agent"
WAIT_AGENT_TOOL      = "wait_agent"
CLOSE_AGENT_TOOL     = "close_agent"

DEFAULT_WAIT_TIMEOUT_MS = 30_000
MIN_WAIT_TIMEOUT_MS     = 10_000
MAX_WAIT_TIMEOUT_MS     = 3_600_000


def subagent_tools(agents: SubagentRuntime) -> list[ClientTool]:
    """返回本地多执行主体控制工具。"""
    return [
        ClientTool(
            name=SPAWN_AGENT_TOOL,
            description=(
                "为边界清晰的独立任务创建子 Agent。子 Agent 继承当前轮次的"
                "运行配置和权限。仅在用户或项目指令允许委派时使用。"
            ),
            input_schema=_spawn_schema(agents.settings.default_fork_turns),
            handler=_spawn_handler(agents),
            meta=_agent_tool_meta(),
        ),
        ClientTool(
            name=LIST_AGENTS_TOOL,
            description=(
                "列出当前根会话中的子 Agent，可按绝对或相对任务路径筛选。"
            ),
            input_schema=_list_schema(),
            handler=_list_handler(agents),
            meta=_agent_tool_meta(),
        ),
        ClientTool(
            name=SEND_MESSAGE_TOOL,
            description=(
                "向目标 Agent 投递轻量消息；活动轮次可接收时"
                "即时送达，否则保留在 mailbox，不创建新轮次。"
            ),
            input_schema=_message_schema("发送给目标 Agent 的消息。"),
            handler=_send_message_handler(agents),
            meta=_agent_tool_meta(),
        ),
        ClientTool(
            name=FOLLOWUP_TASK_TOOL,
            description=(
                "向目标 Agent 追加完整后续任务；空闲时立即启动，"
                "执行中则按顺序排队。"
            ),
            input_schema=_message_schema("追加给目标 Agent 的任务。"),
            handler=_followup_handler(agents),
            meta=_agent_tool_meta(),
        ),
        ClientTool(
            name=INTERRUPT_AGENT_TOOL,
            description=(
                "中断目标 Agent 的当前轮次，不附加消息或创建后续任务。"
            ),
            input_schema=_target_schema("待中断的 Agent 标识或任务路径。"),
            handler=_interrupt_handler(agents),
            meta=_agent_tool_meta(),
        ),
        ClientTool(
            name=RESUME_AGENT_TOOL,
            description=(
                "恢复已经关闭的 Agent，使其可以继续接收后续任务。"
            ),
            input_schema=_resume_schema(),
            handler=_resume_handler(agents),
            meta=_agent_tool_meta(),
        ),
        ClientTool(
            name=WAIT_AGENT_TOOL,
            description=(
                "等待任一目标 Agent 的 mailbox、队列或终态更新。"
            ),
            input_schema=_wait_schema(),
            handler=_wait_handler(agents),
            meta=_agent_tool_meta(),
        ),
        ClientTool(
            name=CLOSE_AGENT_TOOL,
            description=(
                "关闭不再需要的 Agent 及其未关闭后代。已完成的 Agent 在关闭前"
                "仍会占用并发槽位。"
            ),
            input_schema=_close_schema(),
            handler=_close_handler(agents),
            meta=_agent_tool_meta(),
        ),
    ]


def _spawn_handler(agents: SubagentRuntime):
    """创建新执行主体工具处理函数。"""
    async def handle(
        arguments: dict[str, typing.Any],
        tool_runtime: ToolHandlerContext
    ) -> LocalToolResult:
        try:
            message    = _required_text(arguments, "message")
            agent_type = _spawn_agent_type(arguments, tool_runtime)
            task_name  = _required_text(arguments, "task_name")

            fork_turns = normalize_fork_turns(
                arguments.get("fork_turns"),
                default_turns=agents.settings.default_fork_turns,
            )

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
                    "fork_context": {
                        "available_turns": (
                            snapshot.thread.fork_context.available_turns
                        ),
                        "selected_turns": (
                            snapshot.thread.fork_context.selected_turns
                        ),
                        "included_turns": (
                            snapshot.thread.fork_context.included_turns
                        ),
                        "chars": snapshot.thread.fork_context.chars,
                        "truncated": snapshot.thread.fork_context.truncated,
                    },
                },
            )
        except asyncio.CancelledError:
            raise
        except (TypeError, ValueError, RuntimeError) as error:
            return _error_result(SPAWN_AGENT_TOOL, arguments, error)

    return handle


def _list_handler(agents: SubagentRuntime):
    """创建执行主体发现工具处理函数。"""
    async def handle(
        arguments: dict[str, typing.Any],
        tool_runtime: ToolHandlerContext
    ) -> LocalToolResult:
        try:
            caller      = tool_runtime.turn_context.agent
            path_prefix = _optional_text(arguments, "path_prefix")

            snapshots = await agents.list_snapshots(
                caller.root_session_id,
                caller=caller,
                path_prefix=path_prefix,
            )
            items = [_agent_summary(snapshot) for snapshot in snapshots]

            return client_tool_result(
                tool=LIST_AGENTS_TOOL,
                ok=True,
                text=f"listed agents count={len(items)}",
                args=arguments,
                data={"agents": items},
            )
        except asyncio.CancelledError:
            raise
        except (TypeError, ValueError, RuntimeError) as error:
            return _error_result(LIST_AGENTS_TOOL, arguments, error)

    return handle


def _send_message_handler(agents: SubagentRuntime):
    """创建轻量消息投递工具处理函数。"""
    async def handle(
        arguments: dict[str, typing.Any],
        tool_runtime: ToolHandlerContext
    ) -> LocalToolResult:
        try:
            target  = _required_text(arguments, "target")
            message = _required_text(arguments, "message")
            caller  = tool_runtime.turn_context.agent

            dispatch = await agents.send_message(
                caller.root_session_id,
                target,
                message,
                caller=caller,
            )

            event   = dispatch.event
            receipt = dispatch.receipt

            return client_tool_result(
                tool=SEND_MESSAGE_TOOL,
                ok=True,
                text=(
                    f"delivered agent message {event.event_id} "
                    f"via {dispatch.delivery}"
                ),
                args=arguments,
                data={
                    "event_id": event.event_id,
                    "target_agent_id": event.recipient_agent_id,
                    "target_task_path": event.recipient_task_path,
                    "delivery": dispatch.delivery,
                    "receipt": (
                        {
                            "status": receipt.status,
                            "turn_id": receipt.turn_id,
                            "client_message_id": receipt.client_message_id,
                        }
                        if receipt is not None
                        else None
                    ),
                },
            )
        except asyncio.CancelledError:
            raise
        except (TypeError, ValueError, RuntimeError) as error:
            return _error_result(SEND_MESSAGE_TOOL, arguments, error)

    return handle


def _followup_handler(agents: SubagentRuntime):
    """创建后续任务工具处理函数。"""
    async def handle(
        arguments: dict[str, typing.Any],
        tool_runtime: ToolHandlerContext
    ) -> LocalToolResult:
        try:
            target  = _required_text(arguments, "target")
            message = _required_text(arguments, "message")
            caller  = tool_runtime.turn_context.agent

            submission_id = await agents.followup_task(
                caller.root_session_id,
                target,
                message,
                parent_turn_id=tool_runtime.turn_context.turn_id,
                caller=caller,
            )

            return client_tool_result(
                tool=FOLLOWUP_TASK_TOOL,
                ok=True,
                text=f"submitted followup task {submission_id}",
                args=arguments,
                data={"submission_id": submission_id},
            )
        except asyncio.CancelledError:
            raise
        except (TypeError, ValueError, RuntimeError) as error:
            return _error_result(FOLLOWUP_TASK_TOOL, arguments, error)

    return handle


def _interrupt_handler(agents: SubagentRuntime):
    """创建执行主体中断工具处理函数。"""
    async def handle(
        arguments: dict[str, typing.Any],
        tool_runtime: ToolHandlerContext
    ) -> LocalToolResult:
        try:
            target = _required_text(arguments, "target")
            caller = tool_runtime.turn_context.agent

            snapshot = await agents.interrupt(
                caller.root_session_id,
                target,
                caller=caller,
            )
            return client_tool_result(
                tool=INTERRUPT_AGENT_TOOL,
                ok=True,
                text=(
                    f"agent {snapshot.context.task_path} "
                    f"status={snapshot.status}"
                ),
                args=arguments,
                data={
                    "agent_id": snapshot.agent_id,
                    "task_path": snapshot.context.task_path,
                    "status": _agent_status(snapshot),
                },
            )
        except asyncio.CancelledError:
            raise
        except (TypeError, ValueError, RuntimeError) as error:
            return _error_result(INTERRUPT_AGENT_TOOL, arguments, error)

    return handle


def _resume_handler(agents: SubagentRuntime):
    """创建执行主体恢复工具处理函数。"""
    async def handle(
        arguments: dict[str, typing.Any],
        tool_runtime: ToolHandlerContext
    ) -> LocalToolResult:
        try:
            target = _required_text(arguments, "target")
            caller = tool_runtime.turn_context.agent

            snapshot = await agents.resume(
                caller.root_session_id,
                target,
                caller=caller,
            )

            return client_tool_result(
                tool=RESUME_AGENT_TOOL,
                ok=True,
                text=f"agent {snapshot.context.task_path} status={snapshot.status}",
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
        tool_runtime: ToolHandlerContext
    ) -> LocalToolResult:
        try:
            targets    = _targets(arguments.get("targets"))
            caller     = tool_runtime.turn_context.agent
            timeout_ms = _wait_timeout_ms(arguments.get("timeout_ms"))

            waited = await agents.wait_updates(
                caller.root_session_id,
                targets,
                timeout_sec=timeout_ms / 1000,
                caller=caller,
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
                data={
                    "updates": [event.to_dict() for event in waited.events],
                    "status": statuses,
                    "timed_out": waited.timed_out,
                },
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
        tool_runtime: ToolHandlerContext
    ) -> LocalToolResult:
        try:
            target = _required_text(arguments, "target")
            caller = tool_runtime.turn_context.agent

            previous = await agents.close(
                caller.root_session_id,
                target,
                caller=caller,
            )

            return client_tool_result(
                tool=CLOSE_AGENT_TOOL,
                ok=True,
                text=f"closed agent {previous.context.task_path}",
                args=arguments,
                data={"previous_status": _agent_status(previous)},
            )
        except asyncio.CancelledError:
            raise
        except (TypeError, ValueError, RuntimeError) as error:
            return _error_result(CLOSE_AGENT_TOOL, arguments, error)

    return handle


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


def _agent_summary(snapshot: AgentSnapshot) -> dict[str, typing.Any]:
    """转换执行主体快照为发现接口摘要。"""
    context = snapshot.context
    return {
        "agent_id": snapshot.agent_id,
        "agent_type": context.agent_type,
        "task_name": context.task_name,
        "task_path": context.task_path,
        "parent_agent_id": context.parent_agent_id,
        "status": snapshot.status,
        "turn_count": snapshot.turn_count,
        "queued_count": snapshot.queued_count,
    }


def _spawn_agent_type(
    arguments: dict[str, typing.Any],
    tool_runtime: ToolHandlerContext
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


def _optional_text(
    arguments: dict[str, typing.Any],
    field: str,
) -> str | None:
    """读取可选的非空文本参数。"""
    value = arguments.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


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
) -> LocalToolResult:
    """把可预期的控制错误转换为工具失败结果。"""
    text = f"{type(error).__name__}: {error}"

    return client_tool_result(
        tool=tool,
        ok=False,
        text=text,
        args=arguments,
        data={"error": text},
    )


def _agent_tool_meta() -> dict[str, typing.Any]:
    """返回多执行主体工具的通用元数据。"""
    return {
        "hidden" : False,
        "domain" : "client",
        "class"  : "agent"
    }


def _spawn_schema(default_fork_turns: int) -> dict[str, typing.Any]:
    """返回创建工具输入结构。"""
    return {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "交给子 Agent 的初始任务。",
            },
            "task_name": {
                "type": "string",
                "description": "稳定的小写任务名称，仅使用字母、数字和下划线。",
                "pattern": "^[a-z0-9_]+$",
            },
            "agent_type": {
                "type": "string",
                "description": "可选的 Agent 类型覆盖值。",
            },
            "fork_turns": {
                "type": "string",
                "description": (
                    "继承的父会话上下文范围：none、all 或正整数轮次数。"
                ),
                "pattern": "^(none|all|[1-9][0-9]*)$",
                "default": str(default_fork_turns),
            },
        },
        "required": ["message", "task_name"],
        "additionalProperties": False,
    }


def _list_schema() -> dict[str, typing.Any]:
    """返回执行主体发现工具结构。"""
    return {
        "type": "object",
        "properties": {
            "path_prefix": {
                "type": "string",
                "description": "可选的绝对或相对任务路径前缀。",
            },
        },
        "additionalProperties": False,
    }


def _message_schema(description: str) -> dict[str, typing.Any]:
    """返回消息或后续任务工具结构。"""
    return {
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "description": "目标 Agent 标识或任务路径。",
            },
            "message": {
                "type": "string",
                "description": description,
                "maxLength": MAX_AGENT_MESSAGE_CHARS,
            },
        },
        "required": ["target", "message"],
        "additionalProperties": False,
    }


def _resume_schema() -> dict[str, typing.Any]:
    """返回恢复工具输入结构。"""
    return {
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "description": "待恢复的 Agent 标识或任务路径。",
            }
        },
        "required": ["target"],
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
                "description": (
                    "Agent 标识或任务路径列表；任一目标产生更新时返回。"
                ),
            },
            "timeout_ms": {
                "type": "number",
                "description": (
                    f"等待超时（毫秒）。默认 {DEFAULT_WAIT_TIMEOUT_MS}，"
                    f"最小 {MIN_WAIT_TIMEOUT_MS}，最大 {MAX_WAIT_TIMEOUT_MS}。"
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
        "properties": {
            "target": {
                "type": "string",
                "description": "待关闭的 Agent 标识或任务路径。",
            }
        },
        "required": ["target"],
        "additionalProperties": False,
    }


def _target_schema(description: str) -> dict[str, typing.Any]:
    """返回单目标工具输入结构。"""
    return {
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "description": description,
            },
        },
        "required": ["target"],
        "additionalProperties": False,
    }


if __name__ == '__main__':
    pass
