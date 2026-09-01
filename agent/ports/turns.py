# -*- coding: utf-8 -*-

import typing
from collections.abc import (
    Awaitable,
    Callable,
)

from protocol.schema.stream_events import StreamEvent
from protocol.schema.turn_inputs import TurnInput
from protocol.transport.events import EventReport
from .hooks import CommandHookSessionPort
from .mcp_session import McpSessionPort

if typing.TYPE_CHECKING:
    from agent.application.turns.execution import TurnExecution


RetryState: typing.TypeAlias = typing.Literal[
    "idle",
    "transport",
    "provider",
]


class TurnResultPort(typing.Protocol):
    """定义模型轮次结果必须提供的稳定状态。"""

    @property
    def status(self) -> str:
        """返回模型轮次的稳定结束状态。"""
        ...


TurnResultValue = typing.TypeVar(
    "TurnResultValue",
    bound=TurnResultPort,
    covariant=True,
)


class TurnOperation(typing.Protocol[TurnResultValue]):
    """定义在工具会话中执行单个模型轮次的操作端口。"""

    async def __call__(
        self,
        execution: "TurnExecution",
        session: McpSessionPort,
        tools: list[dict[str, typing.Any]],
        event_report: EventReport,
    ) -> TurnResultValue:
        """执行模型轮次并返回稳定结果。"""
        ...


class TurnInputEventHandler(typing.Protocol):
    """定义模型流向子 Agent mailbox 转发输入事件的回调端口。"""

    def __call__(self, event: StreamEvent) -> TurnInput | None:
        """处理一个流式输入事件并返回可确认的 TurnInput。"""
        ...


@typing.runtime_checkable
class TurnCleanupPort(typing.Protocol):
    """定义单轮等待异步资源清理完成的端口。"""

    async def await_cleanup(self, awaitable: Awaitable[None]) -> None:
        """等待清理协程完成并保留取消态收束语义。"""
        ...


class TurnEventReportHandle(typing.Protocol):
    """定义单轮事件报告租约的最小释放接口。"""

    report: EventReport

    async def release(self, *, interrupted: bool) -> None:
        """释放报告租约并按中断状态收束输出。"""
        ...


class TurnEventReportingPort(typing.Protocol):
    """定义轮次获取事件报告租约所需的端口。"""

    async def acquire(
        self,
        cid: str,
        sid: str,
        *,
        lifetime: typing.Any,
    ) -> TurnEventReportHandle:
        """获取当前轮次使用的事件报告租约。"""
        ...


TurnSessionCallback: typing.TypeAlias = Callable[
    [McpSessionPort, list[dict[str, typing.Any]]],
    Awaitable[typing.Any],
]


class TurnExecutionRuntimePort(typing.Protocol):
    """定义执行器访问模型会话和报告生命周期的运行时端口。"""

    event_reporting: TurnEventReportingPort

    async def with_mcp_session(
        self,
        pref_config: dict[str, typing.Any],
        callback: TurnSessionCallback,
    ) -> typing.Any:
        """在已建立的 MCP 会话中执行一次轮次回调。"""
        ...

    def tool_profile_for_turn(self) -> str | None:
        """返回当前轮次的工具过滤档位。"""
        ...

    async def await_cleanup(self, awaitable: Awaitable[typing.Any]) -> typing.Any:
        """等待轮次资源清理并保留取消态收束语义。"""
        ...


@typing.runtime_checkable
class RetryStatePort(typing.Protocol):
    """定义流式重试展示状态的最小端口。"""

    def set_wait_retry_state(self, state: RetryState) -> None:
        """切换等待状态的重试来源。"""
        ...


@typing.runtime_checkable
class TurnAnimationPort(typing.Protocol):
    """定义模型轮次等待动画的最小生命周期端口。"""

    @property
    def active(self) -> bool:
        """返回前台是否正在接管等待动画。"""
        ...

    async def stop_wait(self, *, settle: bool = True) -> None:
        """停止模型轮次等待动画。"""
        ...


@typing.runtime_checkable
class TurnSessionContextPort(typing.Protocol):
    """定义流式准备阶段读取会话输入的最小端口。"""

    @property
    def animate(self) -> bool:
        """返回当前输出是否启用动画。"""
        ...

    @property
    def workspace_root(self) -> str:
        """返回当前轮次用于展示和 Hook 的工作区路径。"""
        ...

    @property
    def hook_startup_warnings(self) -> tuple[str, ...]:
        """返回根会话首次启动时需要展示的 Hook 告警。"""
        ...

    @property
    def command_hook_sessions(self) -> CommandHookSessionPort | None:
        """返回持续命令 Hook 的会话存储端口。"""
        ...

    def capture_environment(
        self,
        *,
        cwd: str,
        workspace_root: str,
    ) -> dict[str, typing.Any] | None:
        """捕获当前轮次使用的客户端环境快照。"""
        ...

    def skills_payload(self) -> list[dict[str, typing.Any]]:
        """返回当前配置的 skills 请求 payload。"""
        ...


@typing.runtime_checkable
class TurnSessionStatePort(typing.Protocol):
    """定义单轮失败上下文和最近回复的会话写回端口。"""

    def queue_turn_context(self, contexts: typing.Iterable[str]) -> None:
        """把未完成轮次的上下文排入下一轮。"""
        ...

    def remember_assistant_reply(self, text: str) -> None:
        """保存最近一次已完成的 assistant 回复。"""
        ...

__all__ = (
    "TurnInputEventHandler",
    "TurnCleanupPort",
    "TurnEventReportHandle",
    "TurnEventReportingPort",
    "TurnExecutionRuntimePort",
    "TurnOperation",
    "RetryState",
    "RetryStatePort",
    "TurnAnimationPort",
    "TurnSessionContextPort",
    "TurnSessionStatePort",
    "TurnResultPort",
    "TurnResultValue",
)
