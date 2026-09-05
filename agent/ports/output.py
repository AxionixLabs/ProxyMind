# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import typing
from abc import (
    ABC,
    abstractmethod,
)
from dataclasses import (
    dataclass,
    field,
)

from .content import (
    ContentSink,
    ResponseIdentity,
)
from .presentation import (
    TextSpan,
    TextStyle,
)

__all__ = (
    "ApprovalCompleted",
    "ApprovalReviewCompleted",
    "ApprovalReviewStarted",
    "ApprovalStarted",
    "AssistantBuffered",
    "AssistantSettled",
    "AssistantVisible",
    "BLOCK_OUTPUT",
    "ContentSink",
    "ModelWaitReason",
    "ModelWaitRequested",
    "OutputControlPort",
    "OutputActivityEvent",
    "OutputActivityPort",
    "OutputDisplay",
    "OutputPort",
    "OutputPresentationPort",
    "OutputSession",
    "OutputSessionFactory",
    "OutputSurfaceContext",
    "PassiveOutputActivity",
    "PresentationSuperseded",
    "RecoveryActivityMode",
    "RecoveryChanged",
    "RetryActivitySource",
    "RetryActivityState",
    "RetryChanged",
    "STREAM_OUTPUT",
    "SurfaceClosed",
    "SurfaceTurnStarted",
    "TerminalWaitCompleted",
    "TerminalWaitStarted",
    "ToolActivityKind",
    "ToolInteractionActivityPort",
    "ToolBatchCompleted",
    "ToolBatchStarted",
    "ToolCompleted",
    "ToolStarted",
    "TurnTerminal",
    "TurnTerminalStatus",
)

OutputDisplay = typing.Literal[
    "stream",
    "block"
]

ModelWaitReason = typing.Literal[
    "initial",
    "server_thinking",
    "tool_result",
    "lifecycle",
    "continuation",
]
ToolActivityKind = typing.Literal[
    "client",
    "builtin",
    "plan",
    "nested",
]
RetryActivitySource = typing.Literal["transport", "provider"]
RetryActivityState = typing.Literal["started", "completed"]
RecoveryActivityMode = typing.Literal[
    "live",
    "replaying",
    "caught_up",
    "gap",
]
TurnTerminalStatus = typing.Literal[
    "completed",
    "failed",
    "interrupted",
    "cancelled",
    "reconciliation_required",
]


@dataclass(frozen=True, slots=True, kw_only=True)
class OutputSurfaceContext:
    """标识一个输出会话观察的本地展示表面和正式 Turn。"""

    surface_id: str
    cid: str
    sid: str
    turn_id: str
    agent_id: str

    def __post_init__(self) -> None:
        """拒绝无法稳定定位输出会话的身份。"""
        for field_name in (
            "surface_id",
            "cid",
            "sid",
            "turn_id",
            "agent_id",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"output surface {field_name} is required")
            object.__setattr__(self, field_name, value.strip())


@dataclass(frozen=True, slots=True, kw_only=True)
class _ScopedActivityEvent:
    """保存所有展示事件共有的输出会话和 Turn 身份。"""

    surface_id: str
    turn_id: str

    def __post_init__(self) -> None:
        """校验展示事件的基础身份。"""
        for field_name in ("surface_id", "turn_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"output activity {field_name} is required")
            object.__setattr__(self, field_name, value.strip())


@dataclass(frozen=True, slots=True, kw_only=True)
class SurfaceTurnStarted(_ScopedActivityEvent):
    """描述输出会话开始观察一个正式 Turn。"""


@dataclass(frozen=True, slots=True, kw_only=True)
class ModelWaitRequested(_ScopedActivityEvent):
    """描述当前 Turn 已进入等待模型继续的展示阶段。"""

    revision: int
    reason: ModelWaitReason

    def __post_init__(self) -> None:
        """校验等待请求版本和来源。"""
        _ScopedActivityEvent.__post_init__(self)
        if (
            isinstance(self.revision, bool)
            or not isinstance(self.revision, int)
            or self.revision < 1
        ):
            raise ValueError("model wait revision must be positive")
        if self.reason not in {
            "initial",
            "server_thinking",
            "tool_result",
            "lifecycle",
            "continuation",
        }:
            raise ValueError("model wait reason is invalid")


@dataclass(frozen=True, slots=True, kw_only=True)
class _AssistantActivityEvent(_ScopedActivityEvent):
    """保存 assistant 展示事实共有的响应和 Item 身份。"""

    identity: ResponseIdentity
    item_id: str

    def __post_init__(self) -> None:
        """校验 assistant 展示身份属于当前 Turn。"""
        _ScopedActivityEvent.__post_init__(self)
        if not isinstance(self.identity, ResponseIdentity):
            raise TypeError("assistant activity identity is required")
        if self.identity.turn_id != self.turn_id:
            raise ValueError("assistant activity identity does not match turn")
        if not isinstance(self.item_id, str) or not self.item_id.strip():
            raise ValueError("assistant activity item_id is required")
        object.__setattr__(self, "item_id", self.item_id.strip())


@dataclass(frozen=True, slots=True, kw_only=True)
class AssistantBuffered(_AssistantActivityEvent):
    """描述 assistant 正文已接收但尚未实际可见。"""


@dataclass(frozen=True, slots=True, kw_only=True)
class AssistantVisible(_AssistantActivityEvent):
    """描述 assistant 正文已经进入活动画布。"""


@dataclass(frozen=True, slots=True, kw_only=True)
class AssistantSettled(_AssistantActivityEvent):
    """描述 assistant 正文段已经稳定。"""


@dataclass(frozen=True, slots=True, kw_only=True)
class PresentationSuperseded(_ScopedActivityEvent):
    """描述旧展示代次已被新的 Worker 展示代次替代。"""

    superseded_epoch: int
    presentation_epoch: int

    def __post_init__(self) -> None:
        """校验展示代次只能单调替代。"""
        _ScopedActivityEvent.__post_init__(self)
        for field_name in ("superseded_epoch", "presentation_epoch"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"presentation {field_name} must be positive")
        if self.presentation_epoch <= self.superseded_epoch:
            raise ValueError("presentation epoch must advance")


@dataclass(frozen=True, slots=True, kw_only=True)
class _BatchActivityEvent(_ScopedActivityEvent):
    """保存工具批次展示事实的稳定身份。"""

    batch_id: str

    def __post_init__(self) -> None:
        """校验批次身份。"""
        _ScopedActivityEvent.__post_init__(self)
        if not isinstance(self.batch_id, str) or not self.batch_id.strip():
            raise ValueError("tool batch_id is required")
        object.__setattr__(self, "batch_id", self.batch_id.strip())


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolBatchStarted(_BatchActivityEvent):
    """描述工具批次已经登记。"""


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolBatchCompleted(_BatchActivityEvent):
    """描述工具批次事件已经完整。"""


@dataclass(frozen=True, slots=True, kw_only=True)
class _ToolActivityEvent(_ScopedActivityEvent):
    """保存单个工具展示事实的类型和稳定身份。"""

    tool_id: str
    tool_kind: ToolActivityKind
    name: str = ""

    def __post_init__(self) -> None:
        """校验工具身份并规范化可选名称。"""
        _ScopedActivityEvent.__post_init__(self)
        if not isinstance(self.tool_id, str) or not self.tool_id.strip():
            raise ValueError("tool activity identity is required")
        if self.tool_kind not in {"client", "builtin", "plan", "nested"}:
            raise ValueError("tool activity kind is invalid")
        object.__setattr__(self, "tool_id", self.tool_id.strip())
        object.__setattr__(self, "name", str(self.name or "").strip())


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolStarted(_ToolActivityEvent):
    """描述一个工具取得具名活动 lease。"""


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolCompleted(_ToolActivityEvent):
    """描述一个工具释放具名活动 lease。"""


@dataclass(frozen=True, slots=True, kw_only=True)
class _TerminalWaitActivityEvent(_ScopedActivityEvent):
    """保存后台终端等待的调用和进程会话身份。"""

    call_id: str
    session_id: str
    command: str = ""

    def __post_init__(self) -> None:
        """校验终端等待身份。"""
        _ScopedActivityEvent.__post_init__(self)
        for field_name in ("call_id", "session_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"terminal wait {field_name} is required")
            object.__setattr__(self, field_name, value.strip())
        object.__setattr__(self, "command", str(self.command or "").strip())


@dataclass(frozen=True, slots=True, kw_only=True)
class TerminalWaitStarted(_TerminalWaitActivityEvent):
    """描述后台终端开始等待输出。"""


@dataclass(frozen=True, slots=True, kw_only=True)
class TerminalWaitCompleted(_TerminalWaitActivityEvent):
    """描述后台终端结束等待输出。"""


@dataclass(frozen=True, slots=True, kw_only=True)
class _ApprovalActivityEvent(_ScopedActivityEvent):
    """保存审批展示事实的审批和调用身份。"""

    approval_id: str
    call_id: str

    def __post_init__(self) -> None:
        """校验审批身份。"""
        _ScopedActivityEvent.__post_init__(self)
        for field_name in ("approval_id", "call_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"approval activity {field_name} is required")
            object.__setattr__(self, field_name, value.strip())


@dataclass(frozen=True, slots=True, kw_only=True)
class ApprovalStarted(_ApprovalActivityEvent):
    """描述审批表面取得独占交互权。"""


@dataclass(frozen=True, slots=True, kw_only=True)
class ApprovalCompleted(_ApprovalActivityEvent):
    """描述审批表面释放独占交互权。"""


@dataclass(frozen=True, slots=True, kw_only=True)
class _ApprovalReviewActivityEvent(_ScopedActivityEvent):
    """保存自动评审活动的独立身份和动作摘要。"""

    review_id: str
    approval_id: str
    call_id: str
    action_summary: str
    presentation_epoch: int

    def __post_init__(self) -> None:
        """校验评审 lease 不复用审批卡身份。"""
        _ScopedActivityEvent.__post_init__(self)
        for field_name in (
            "review_id",
            "approval_id",
            "call_id",
            "action_summary",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"approval review {field_name} is required")
            object.__setattr__(self, field_name, value.strip())
        if (
            isinstance(self.presentation_epoch, bool)
            or not isinstance(self.presentation_epoch, int)
            or self.presentation_epoch < 1
        ):
            raise ValueError("approval review presentation_epoch must be positive")


@dataclass(frozen=True, slots=True, kw_only=True)
class ApprovalReviewStarted(_ApprovalReviewActivityEvent):
    """描述一项自动审批评审取得活动 lease。"""


@dataclass(frozen=True, slots=True, kw_only=True)
class ApprovalReviewCompleted(_ApprovalReviewActivityEvent):
    """描述一项自动审批评审释放活动 lease。"""


@dataclass(frozen=True, slots=True, kw_only=True)
class RetryChanged(_ScopedActivityEvent):
    """描述一个独立重试来源开始或结束。"""

    source: RetryActivitySource
    state: RetryActivityState
    presentation_epoch: int
    round: int
    attempt: int

    def __post_init__(self) -> None:
        """校验 retry Attempt 身份。"""
        _ScopedActivityEvent.__post_init__(self)
        if self.source not in {"transport", "provider"}:
            raise ValueError("retry activity source is invalid")
        if self.state not in {"started", "completed"}:
            raise ValueError("retry activity state is invalid")
        for field_name in ("presentation_epoch", "round", "attempt"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"retry {field_name} must be positive")


@dataclass(frozen=True, slots=True, kw_only=True)
class RecoveryChanged(_ScopedActivityEvent):
    """描述事件流恢复投影模式和当前持久水位。"""

    mode: RecoveryActivityMode
    event_seq: int

    def __post_init__(self) -> None:
        """校验恢复水位。"""
        _ScopedActivityEvent.__post_init__(self)
        if self.mode not in {"live", "replaying", "caught_up", "gap"}:
            raise ValueError("recovery activity mode is invalid")
        if (
            isinstance(self.event_seq, bool)
            or not isinstance(self.event_seq, int)
            or self.event_seq < 0
        ):
            raise ValueError("recovery event_seq must be non-negative")


@dataclass(frozen=True, slots=True, kw_only=True)
class TurnTerminal(_ScopedActivityEvent):
    """描述当前 Turn 已收到权威或确定的终态。"""

    status: TurnTerminalStatus

    def __post_init__(self) -> None:
        """校验终态分类属于正式客户端契约。"""
        _ScopedActivityEvent.__post_init__(self)
        if self.status not in {
            "completed",
            "failed",
            "interrupted",
            "cancelled",
            "reconciliation_required",
        }:
            raise ValueError("turn terminal status is invalid")


@dataclass(frozen=True, slots=True, kw_only=True)
class SurfaceClosed(_ScopedActivityEvent):
    """描述输出会话已经关闭。"""


OutputActivityEvent: typing.TypeAlias = (
    SurfaceTurnStarted
    | ModelWaitRequested
    | AssistantBuffered
    | AssistantVisible
    | AssistantSettled
    | PresentationSuperseded
    | ToolBatchStarted
    | ToolBatchCompleted
    | ToolStarted
    | ToolCompleted
    | TerminalWaitStarted
    | TerminalWaitCompleted
    | ApprovalStarted
    | ApprovalCompleted
    | ApprovalReviewStarted
    | ApprovalReviewCompleted
    | RetryChanged
    | RecoveryChanged
    | TurnTerminal
    | SurfaceClosed
)


class OutputActivityPort(typing.Protocol):
    """接收单个 OutputSession 的展示事实并拥有其派生资源。"""

    async def open(self) -> None:
        """启动当前输出表面的展示生命周期。"""
        ...

    async def emit(self, event: OutputActivityEvent) -> None:
        """按身份和顺序接收一项展示事实。"""
        ...

    async def emit_batch(
        self,
        events: tuple[OutputActivityEvent, ...],
    ) -> None:
        """原子接收一组非空有序事实，只向展示层提交最终归约结果。"""
        ...

    async def close(self) -> None:
        """幂等取消当前表面的 timer、lease 和回调。"""
        ...


class ToolInteractionActivityPort(typing.Protocol):
    """投影 Harness 内嵌套工具和审批的具名活动生命周期。"""

    async def tool_started(
        self,
        tool_id: str,
        tool_kind: ToolActivityKind,
        *,
        name: str = "",
    ) -> None:
        """为工具取得具名活动 lease。"""
        ...

    async def tool_completed(
        self,
        tool_id: str,
        tool_kind: ToolActivityKind,
        *,
        name: str = "",
    ) -> None:
        """释放匹配的工具活动 lease。"""
        ...

    async def approval_started(self, approval_id: str, call_id: str) -> None:
        """为审批取得独占交互权。"""
        ...

    async def approval_completed(self, approval_id: str, call_id: str) -> None:
        """释放匹配的审批交互权。"""
        ...


class PassiveOutputActivity(OutputActivityPort):
    """为没有活动展示表面的输出模式提供显式空实现。"""

    async def open(self) -> None:
        """忽略展示生命周期启动。"""
        return None

    async def emit(self, event: OutputActivityEvent) -> None:
        """忽略已类型化的展示事实。"""
        _ = event
        return None

    async def emit_batch(
        self,
        events: tuple[OutputActivityEvent, ...],
    ) -> None:
        """忽略原子提交的已类型化展示事实。"""
        if not events:
            raise ValueError("output activity batch cannot be empty")
        return None

    async def close(self) -> None:
        """忽略展示生命周期关闭。"""
        return None

STREAM_OUTPUT: typing.Final[OutputDisplay] = "stream"
BLOCK_OUTPUT: typing.Final[OutputDisplay] = "block"

PresentationViewT = typing.TypeVar(
    "PresentationViewT",
    contravariant=True,
)


class OutputControlPort(ABC):
    """描述单轮运行需要的输出生命周期和审计能力。"""

    @abstractmethod
    async def open(self) -> None:
        """打开输出会话。"""
        ...

    @abstractmethod
    async def stop(self, *, blink: bool = True) -> None:
        """停止输出会话并释放资源。"""
        ...

    @abstractmethod
    async def record_hidden_output(self, text: str) -> None:
        """记录不直接展示的输出内容。"""
        ...

    @abstractmethod
    def record_tool_arguments(
        self,
        name: str,
        arguments: dict[str, typing.Any],
        *,
        call_id: str | None = None,
    ) -> None:
        """记录工具参数审计信息。"""
        ...


class OutputPort(OutputControlPort):
    """描述终端渲染适配器需要的完整输出能力。"""

    @property
    @abstractmethod
    def terminal_width(self) -> int | None:
        """返回当前终端宽度。"""
        ...

    @property
    @abstractmethod
    def terminal_height(self) -> int | None:
        """返回当前终端高度。"""
        ...

    @abstractmethod
    async def prepare_external_output(self) -> None:
        """准备输出外部内容。"""
        ...

    @abstractmethod
    async def settle_stream(self) -> None:
        """同步当前流式正文。"""
        ...

    @abstractmethod
    def mark_stream_boundary(self) -> None:
        """标记下一段流式边界。"""
        ...

    @abstractmethod
    async def feed(
        self,
        chunk: str | None,
        *,
        echo: bool = True,
        display: OutputDisplay = STREAM_OUTPUT,
        display_chunk: str | None = None,
        display_style: TextStyle | None = None,
        display_parts: list[TextSpan] | None = None,
        preserve_display_parts: bool = False,
    ) -> None:
        """追加一段流式或块状输出。"""
        ...

    @abstractmethod
    async def print_block(
        self,
        chunk: str | None,
        *,
        display_parts: list[TextSpan] | None = None,
    ) -> None:
        """直接输出块文本。"""
        ...

    @abstractmethod
    def flush(self) -> None:
        """刷新输出缓冲区。"""
        ...


class OutputPresentationPort(typing.Protocol[PresentationViewT]):
    """接收由 application view 生成的结构化输出。"""

    async def emit(self, view: PresentationViewT) -> None:
        """发送一项结构化输出。"""
        ...


@dataclass(slots=True)
class OutputSession(typing.Generic[PresentationViewT]):
    """聚合并关闭单轮输出控制、展示事实和投影端口。"""

    context: OutputSurfaceContext
    control: OutputControlPort
    activity: OutputActivityPort
    content: ContentSink
    presentation: OutputPresentationPort[PresentationViewT]
    show_hook_lifecycle: bool = False
    _opened: bool = field(default=False, init=False, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)
    _lifecycle_lock: asyncio.Lock = field(
        default_factory=asyncio.Lock,
        init=False,
        repr=False,
    )

    async def open(self) -> None:
        """按 activity、输出控制顺序幂等启动会话。"""
        async with self._lifecycle_lock:
            if self._closed:
                raise RuntimeError("output session is closed")
            if self._opened:
                return None
            await self.activity.open()
            try:
                await self.control.open()
            except BaseException as error:
                try:
                    await self.activity.close()
                except BaseException as cleanup_error:
                    error.add_note(
                        "output activity close failed after open error: "
                        f"{type(cleanup_error).__name__}: {cleanup_error}"
                    )
                raise
            self._opened = True

    @property
    def is_open(self) -> bool:
        """返回输出控制和活动表面是否已经完整打开且仍可接收投影。"""
        return self._opened and not self._closed

    async def close(self, *, blink: bool = True) -> None:
        """幂等关闭展示事实和输出控制，且不因前一步失败跳过后一步。"""
        async with self._lifecycle_lock:
            if self._closed:
                return None
            self._closed = True
            primary_error: BaseException | None = None
            try:
                await self.control.stop(blink=blink)
            except BaseException as error:
                primary_error = error
            try:
                await self.activity.close()
            except BaseException as error:
                if primary_error is None:
                    primary_error = error
                else:
                    primary_error.add_note(
                        "output activity close also failed: "
                        f"{type(error).__name__}: {error}"
                    )
            if primary_error is not None:
                raise primary_error


class OutputSessionFactory(typing.Protocol[PresentationViewT]):
    """定义按记录路径创建输出会话的工厂端口。"""

    def __call__(
        self,
        log_file: str,
        *,
        context: OutputSurfaceContext,
        animate: bool = True,
    ) -> OutputSession[PresentationViewT]:
        """创建绑定输出记录和前端展示端口的会话。"""
        ...


if __name__ == '__main__':
    pass
