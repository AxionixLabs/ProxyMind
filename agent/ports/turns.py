# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from collections.abc import (
    Awaitable,
    Callable,
)

from agent.domain.policies import PermissionSettings
from protocol.schema.stream_events import StreamEvent
from protocol.schema.turn_inputs import TurnInput
from .approvals import ApprovalLedger
from .hooks import (
    CommandHookSessionPort,
    HookScopeProviderPort,
)
from .mcp_session import McpSessionPort
from .permissions import PermissionGrantReader

if typing.TYPE_CHECKING:
    from agent.application.turns.execution import TurnExecution

__all__ = (
    "TurnInputEventHandler",
    "TurnCleanupPort",
    "TurnEventReportHandle",
    "TurnEventReportingPort",
    "TurnExecutionRuntimePort",
    "TurnStartResultPort",
    "RootTurnSessionPort",
    "TurnOperation",
    "EventReportPort",
    "EventReportLifetime",
    "TurnSessionContextPort",
    "TurnSessionStatePort",
    "TurnResultPort",
    "TurnResultValue",
)


EventReportLifetime: typing.TypeAlias = typing.Literal[
    "turn",
    "session",
]


class EventReportPort(typing.Protocol):
    """定义单轮事件报告的最小传输边界及调用方生命周期约束。"""

    def begin_turn(
        self,
        turn_id: str | None = None,
        *,
        round_no: int | None = None,
    ) -> str:
        """绑定新的逻辑轮次身份并返回最终 Turn ID。"""
        ...

    def bind_event(self, event: StreamEvent) -> None:
        """同步权威事件携带的协议与模型轮次元数据。"""
        ...

    def emit(self, event: dict[str, typing.Any]) -> None:
        """按当前报告上下文登记一条待发送事件。"""
        ...

    async def flush(self) -> None:
        """等待当前已登记事件完成发送。"""
        ...


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
        event_report: EventReportPort,
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

    report: EventReportPort

    async def release(self, *, interrupted: bool) -> None:
        """释放报告租约并按中断状态收束输出。"""
        ...


class TurnEventReportingPort(typing.Protocol):
    """定义应用生命周期内事件报告租约的获取与释放端口。"""

    async def acquire(
        self,
        cid: str,
        sid: str,
        *,
        lifetime: EventReportLifetime,
    ) -> TurnEventReportHandle:
        """获取当前轮次使用的事件报告租约。"""
        ...

    async def close_session(
        self,
        cid: str,
        sid: str,
        *,
        drain: bool = True,
    ) -> None:
        """关闭指定根会话持有的报告资源。"""
        ...

    async def close(self) -> None:
        """关闭所有报告资源并阻止后续租约创建。"""
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


class TurnStartResultPort(typing.Protocol):
    """定义根会话登记后返回的轮次边界快照。"""

    cid: str
    sid: str
    session_started: bool
    start_reason: str
    additional_context: tuple[str, ...]
    system_message: str

    def metadata(self) -> dict[str, str]:
        """返回登记后的会话坐标。"""
        ...


class RootTurnSessionPort(typing.Protocol):
    """定义根轮次准备读取会话资源和 Hook 作用域的端口。"""

    @property
    def workspace_root(self) -> str:
        """返回根轮次绑定的工作区。"""
        ...

    @property
    def permission_grants(self) -> PermissionGrantReader | None:
        """返回当前会话的权限授予读取端口。"""
        ...

    @property
    def output_record_path(self) -> str:
        """返回根轮次输出记录路径。"""
        ...

    @property
    def permissions(self) -> PermissionSettings:
        """返回当前根会话的默认权限设置。"""
        ...

    @property
    def approval_ledger(self) -> ApprovalLedger | None:
        """返回根轮次使用的审批调用账本。"""
        ...

    async def fresh_pref_config(
        self,
        *,
        ttl_sec: float,
    ) -> dict[str, typing.Any]:
        """读取当前有效的偏好配置快照。"""
        ...

    async def begin_turn(
        self,
        cid: str | None,
        sid: str | None,
        *,
        title: str,
        source: str,
    ) -> TurnStartResultPort:
        """登记或续用根会话并返回轮次边界快照。"""
        ...

    def transcript_path_for_session(self, sid: str) -> str:
        """返回指定会话的 Transcript 路径。"""
        ...

    @property
    def hook_scope_provider(self) -> HookScopeProviderPort:
        """返回根轮次使用的 Hook 作用域提供器。"""
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


if __name__ == '__main__':
    pass
