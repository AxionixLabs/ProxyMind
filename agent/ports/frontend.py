# -*- coding: utf-8 -*-

import typing

from .output import OutputSessionFactory
from .presentation import ApplicationSink
from .turns import RetryState

ActivityStatusKind = typing.Literal[
    "wait",
    "upload",
    "download",
    "inbuild",
    "external_mcp",
    "compact",
    "operation",
]

ActivitySnapshot = typing.Callable[[], dict[str, typing.Any]]


class ActivityRuntimePort(typing.Protocol):
    """描述应用活动状态的展示生命周期。

    实现方拥有具体前端的活动区域和终端进度；调用方只能按活动类型开始、冻结或
    结束展示，不得读取具体 TUI、桌面端或 Web 状态。
    """

    @property
    def active(self) -> bool:
        """返回当前前端是否接管活动状态。"""
        ...

    def begin_terminal_progress(self) -> None:
        """启动终端窗口的运行进度。"""
        ...

    def end_terminal_progress(self) -> None:
        """清除终端窗口的运行进度。"""
        ...

    def set_wait_retry_state(self, state: RetryState) -> None:
        """切换等待状态的重试来源。"""
        ...

    def finish_turn_wait(self) -> None:
        """结束当前轮次拥有的等待状态。"""
        ...

    async def open(self) -> None:
        """启动前端运行期。"""
        ...

    async def close(self) -> None:
        """停止前端运行期。"""
        ...

    async def begin_wait_status(self) -> None:
        """显示轮次等待状态。"""
        ...

    async def ensure_wait_status_for_turn(self) -> None:
        """确保等待状态已经接管当前轮次。"""
        ...

    async def begin_upload_status(self, snapshot: ActivitySnapshot) -> None:
        """显示附件上传状态。"""
        ...

    async def begin_download_status(self, snapshot: ActivitySnapshot) -> None:
        """显示运行时下载状态。"""
        ...

    async def begin_inbuild_status(self, snapshot: ActivitySnapshot) -> None:
        """显示内置服务启动状态。"""
        ...

    async def begin_external_mcp_status(
        self,
        snapshot: ActivitySnapshot,
    ) -> None:
        """显示外部 MCP 启动状态。"""
        ...

    async def begin_compact_status(self, snapshot: ActivitySnapshot) -> None:
        """显示对话压缩状态。"""
        ...

    async def begin_operation_status(self, snapshot: ActivitySnapshot) -> None:
        """显示通用前台操作状态。"""
        ...

    async def end_activity_status(
        self,
        kind: ActivityStatusKind | None = None,
        *,
        settle: bool = True,
    ) -> None:
        """结束当前活动状态。"""
        ...

    async def freeze_activity_status(self, kind: ActivityStatusKind) -> None:
        """冻结活动状态并等待后续可见结果接管。"""
        ...


@typing.runtime_checkable
class FrontendActivityPort(typing.Protocol):
    """定义前端活动展示的高层生命周期。"""

    @property
    def active(self) -> bool:
        """返回具体前端是否接管活动展示。"""
        ...

    @property
    def enabled(self) -> bool:
        """返回当前入口是否启用活动展示。"""
        ...

    async def start_wait(self) -> None:
        """开始模型等待展示。"""
        ...

    async def start_upload(self, snapshot: ActivitySnapshot) -> None:
        """开始附件上传展示。"""
        ...

    async def start_inbuild(self, snapshot: ActivitySnapshot) -> None:
        """开始内置服务启动展示。"""
        ...

    async def start_external_mcp(self, snapshot: ActivitySnapshot) -> None:
        """开始外部 MCP 启动展示。"""
        ...

    async def start_compact(self, snapshot: ActivitySnapshot) -> None:
        """开始压缩展示。"""
        ...

    async def stop(
        self,
        kind: ActivityStatusKind | None = None,
        *,
        settle: bool = True,
    ) -> None:
        """结束指定活动展示。"""
        ...

    async def freeze(self, kind: ActivityStatusKind) -> None:
        """冻结指定活动展示。"""
        ...


@typing.runtime_checkable
class FrontendPort(typing.Protocol):
    """聚合应用宿主跨前端稳定使用的展示和输出能力。"""

    @property
    def application(self) -> ApplicationSink:
        """返回应用级展示端口。"""
        ...

    @property
    def session_factory(self) -> OutputSessionFactory:
        """返回单轮输出会话工厂。"""
        ...

    @property
    def runtime(self) -> ActivityRuntimePort:
        """返回活动状态生命周期端口。"""
        ...


class AttachmentStatePort(typing.Protocol):
    """描述一次用户提交前的本地附件暂存能力。

    实现方负责本地路径解析和正文读取；应用宿主只持有该端口，不解释文件格式或
    构造具体附件实现。
    """

    def has_pending_attachments(self) -> bool:
        """返回是否存在待提交附件。"""
        ...

    def pending_attachments_snapshot(self) -> list[dict[str, typing.Any]]:
        """返回待提交附件的独立快照。"""
        ...

    def replace_pending_attachments(
        self,
        attachments: typing.Iterable[typing.Mapping[str, typing.Any]],
    ) -> None:
        """替换全部待提交附件。"""
        ...

    def add_pending_attachments(self, raw_path: str) -> dict[str, typing.Any]:
        """解析并添加一组本地附件。"""
        ...

    def remove_pending_attachment(self, query: str) -> dict[str, typing.Any]:
        """移除一个待提交附件。"""
        ...

    def clear_pending_attachments(self) -> int:
        """清空待提交附件并返回数量。"""
        ...

    def consume_pending_attachments(self) -> list[dict[str, typing.Any]]:
        """读取并消费待提交附件。"""
        ...


class TurnCompletionPresenterPort(typing.Protocol):
    """定义一次前台轮次完成后的展示投影。"""

    def __call__(
        self,
        application: ApplicationSink,
        elapsed_seconds: float,
    ) -> None:
        """提交轮次完成展示。"""
        ...


__all__ = (
    "ActivityRuntimePort",
    "ActivitySnapshot",
    "ActivityStatusKind",
    "AttachmentStatePort",
    "FrontendActivityPort",
    "FrontendPort",
    "TurnCompletionPresenterPort",
)
