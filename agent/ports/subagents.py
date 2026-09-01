# -*- coding: utf-8 -*-

import typing
from collections.abc import (
    Awaitable,
    Callable,
    Iterable,
    Mapping,
)

from protocol.schema.stream_events import StreamEvent
from protocol.schema.turn_inputs import TurnInput
from .mcp_session import McpSessionPort
from .turns import (
    EventReportPort,
    TurnInputEventHandler,
)

SkillsProvider: typing.TypeAlias = Callable[[], list[dict[str, str]]]

if typing.TYPE_CHECKING:
    from agent.application.agents.fork_context import ForkTurns
    from agent.application.agents.messages import AgentMessageDispatch
    from agent.application.agents.views import (
        AgentMailboxWaitResult,
        AgentSnapshot,
    )
    from agent.application.turns.context import AgentContext
    from agent.application.turns.run_result import RunResult
    from agent.application.turns.execution import TurnExecution
    from agent.application.turns.context import TurnContext
    from .hooks import HookScopeProviderPort


class SubagentExecutionPort(typing.Protocol):
    """定义执行已经准备好的子模型轮次所需能力。"""

    async def execute(
        self,
        pref_config: dict[str, typing.Any],
        skills: list[dict[str, str]],
        execution: "TurnExecution",
        session: McpSessionPort,
        tools: list[dict[str, typing.Any]],
        event_report: EventReportPort,
        on_turn_input_event: (
            typing.Callable[[StreamEvent], TurnInput | None] | None
        ) = None,
    ) -> "RunResult":
        """执行子模型轮次并返回结构化结果。"""
        ...


class SubagentStreamPort(typing.Protocol):
    """定义流式模型适配器执行子 Agent Turn 的端口。"""

    async def __call__(
        self,
        session: McpSessionPort,
        pref_config: dict[str, typing.Any],
        tools: list[dict[str, typing.Any]],
        *,
        turn_execution: "TurnExecution",
        event_report: EventReportPort,
        skills: list[dict[str, str]],
        on_turn_input_event: TurnInputEventHandler | None = None,
    ) -> "RunResult":
        """执行流式子轮次并返回结构化结果。"""
        ...


SubagentResultValue = typing.TypeVar(
    "SubagentResultValue",
    bound="RunResult",
    covariant=True,
)


class SubagentOperation(typing.Protocol[SubagentResultValue]):
    """定义使用固定 Hook 作用域执行子轮次的操作端口。"""

    async def __call__(
        self,
        execution: "TurnExecution",
        session: McpSessionPort,
        tools: list[dict[str, typing.Any]],
        event_report: EventReportPort,
    ) -> SubagentResultValue:
        """执行子轮次并返回稳定结果。"""
        ...


class SubagentTurnRunner(typing.Protocol):
    """定义 Harness 调用一次子 Agent Turn 的端口。"""

    async def __call__(
        self,
        pref_config: dict[str, typing.Any],
        execution: "TurnExecution",
        operation: SubagentOperation["RunResult"],
        *,
        event_report: EventReportPort | None = None,
    ) -> "RunResult":
        """运行固定子轮次并返回结果。"""
        ...


class SubagentCleanupPort(typing.Protocol):
    """定义 Harness 等待异步清理收束的端口。"""

    async def await_cleanup(self, awaitable: Awaitable[None]) -> None:
        """等待清理协程完成。"""
        ...


class SubagentControlPort(typing.Protocol):
    """定义本地 Agent 控制工具可调用的 Harness 操作边界。"""

    @property
    def enabled(self) -> bool:
        """返回当前运行时是否开放子 Agent 控制。"""
        ...

    @property
    def default_fork_turns(self) -> int:
        """返回创建子 Agent 时的默认继承轮次数。"""
        ...

    async def spawn(
        self,
        parent: "TurnContext",
        message: str,
        pref_config: Mapping[str, typing.Any],
        *,
        agent_type: str,
        task_name: str,
        fork_turns: "ForkTurns | None" = None,
    ) -> "AgentSnapshot":
        """创建子 Agent 并返回初始快照。"""
        ...

    async def list_snapshots(
        self,
        root_session_id: str,
        *,
        caller: "AgentContext",
        path_prefix: str | None = None,
    ) -> tuple["AgentSnapshot", ...]:
        """列出调用者可见的 Agent 快照。"""
        ...

    async def send_message(
        self,
        root_session_id: str,
        target: str,
        message: str,
        *,
        caller: "AgentContext",
    ) -> "AgentMessageDispatch":
        """向目标 Agent 的活动轮次或 mailbox 投递消息。"""
        ...

    async def followup_task(
        self,
        root_session_id: str,
        target: str,
        message: str,
        *,
        parent_turn_id: str,
        caller: "AgentContext",
    ) -> str:
        """向目标 Agent 提交后续任务。"""
        ...

    async def interrupt(
        self,
        root_session_id: str,
        target: str,
        *,
        caller: "AgentContext",
    ) -> "AgentSnapshot":
        """中断目标 Agent 的当前轮次。"""
        ...

    async def resume(
        self,
        root_session_id: str,
        target: str,
        *,
        caller: "AgentContext",
    ) -> "AgentSnapshot":
        """重新开放已关闭的目标 Agent。"""
        ...

    async def wait_updates(
        self,
        root_session_id: str,
        targets: Iterable[str],
        *,
        timeout_sec: float | None,
        caller: "AgentContext",
    ) -> "AgentMailboxWaitResult":
        """等待目标 Agent 的 mailbox、队列或终态更新。"""
        ...

    async def close(
        self,
        root_session_id: str,
        target: str,
        *,
        caller: "AgentContext",
    ) -> "AgentSnapshot":
        """关闭目标 Agent 及其未关闭后代。"""
        ...


class SubagentRuntimeHostPort(typing.Protocol):
    """定义 SubagentRuntime 所需的组合根宿主端口。"""

    @property
    def subagent_execution(self) -> SubagentExecutionPort:
        """返回子 Agent 的模型执行端口。"""
        ...

    @property
    def subagent_turn_runner(self) -> SubagentTurnRunner:
        """返回一次子 Agent Turn 的执行端口。"""
        ...

    @property
    def subagent_cleanup(self) -> SubagentCleanupPort:
        """返回子 Agent 轮次的异步清理端口。"""
        ...

    @property
    def hook_scope_provider(self) -> "HookScopeProviderPort":
        """返回子 Agent 轮次使用的 Hook 作用域提供器。"""
        ...


__all__ = (
    "SkillsProvider",
    "SubagentExecutionPort",
    "SubagentStreamPort",
    "SubagentOperation",
    "SubagentResultValue",
    "SubagentTurnRunner",
    "SubagentCleanupPort",
    "SubagentControlPort",
    "SubagentRuntimeHostPort",
)
