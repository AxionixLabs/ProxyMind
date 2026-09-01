# -*- coding: utf-8 -*-

import typing
from collections.abc import Awaitable

from protocol.schema.stream_events import StreamEvent
from protocol.schema.turn_inputs import TurnInput
from protocol.transport.events import EventReport
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
    "TurnOperation",
    "RetryState",
    "RetryStatePort",
    "TurnAnimationPort",
    "TurnSessionContextPort",
    "TurnSessionStatePort",
    "TurnResultPort",
    "TurnResultValue",
)
