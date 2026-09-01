# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from dataclasses import (
    dataclass,
    field
)
from agent.ports import (
    ActivityRuntimePort,
    ActivitySnapshot,
    ActivityStatusKind,
    OutputSessionFactory,
    RetryState,
)
from agent.ports.presentation import ApplicationSink
from frontends.interaction.contracts import InteractionPort

class PassiveFrontendRuntime(ActivityRuntimePort):
    """提供无需常驻前端运行期时的空实现。"""

    @property
    def active(self) -> bool:
        """返回未接管终端状态。"""
        return False

    def set_wait_retry_state(self, state: RetryState) -> None:
        """忽略等待状态的重试来源。"""
        _ = state
        return None

    def begin_terminal_progress(self) -> None:
        """忽略终端窗口进度开始请求。"""
        return None

    def end_terminal_progress(self) -> None:
        """忽略终端窗口进度清理请求。"""
        return None

    def finish_turn_wait(self) -> None:
        """忽略模型轮次等待状态生命周期结束请求。"""
        return None

    async def open(self) -> None:
        """忽略启动请求。"""
        return None

    async def close(self) -> None:
        """忽略停止请求。"""
        return None

    async def begin_wait_status(self) -> None:
        """忽略等待状态请求。"""
        return None

    async def ensure_wait_status_for_turn(self) -> None:
        """忽略模型轮次等待状态交接请求。"""
        return None

    async def begin_upload_status(
        self,
        snapshot: ActivitySnapshot,
    ) -> None:
        """忽略上传状态请求。"""
        _ = snapshot
        return None

    async def begin_download_status(
        self,
        snapshot: ActivitySnapshot,
    ) -> None:
        """忽略运行时下载状态请求。"""
        _ = snapshot
        return None

    async def begin_inbuild_status(
        self,
        snapshot: ActivitySnapshot,
    ) -> None:
        """忽略内置运行时状态请求。"""
        _ = snapshot
        return None

    async def begin_external_mcp_status(
        self,
        snapshot: ActivitySnapshot,
    ) -> None:
        """忽略外部 MCP 状态请求。"""
        _ = snapshot
        return None

    async def begin_compact_status(
        self,
        snapshot: ActivitySnapshot,
    ) -> None:
        """忽略对话压缩状态请求。"""
        _ = snapshot
        return None

    async def begin_operation_status(
        self,
        snapshot: ActivitySnapshot,
    ) -> None:
        """忽略通用前台操作状态请求。"""
        _ = snapshot
        return None

    async def end_activity_status(
        self,
        kind: ActivityStatusKind | None = None,
        *,
        settle: bool = True,
    ) -> None:
        """忽略活动状态结束请求。"""
        _ = kind
        _ = settle
        return None

    async def freeze_activity_status(
        self,
        kind: ActivityStatusKind,
    ) -> None:
        """忽略活动状态冻结请求。"""
        _ = kind
        return None


@dataclass(frozen=True, slots=True)
class Frontend(object):
    """聚合应用级展示、交互和单轮输出装配能力。"""
    application: ApplicationSink
    interaction: InteractionPort
    session_factory: OutputSessionFactory
    runtime: ActivityRuntimePort = field(default_factory=PassiveFrontendRuntime)


if __name__ == '__main__':
    pass
