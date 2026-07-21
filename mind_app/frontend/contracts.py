# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from abc import (
    ABC,
    abstractmethod
)
from dataclasses import (
    dataclass,
    field
)
from mind_app.interaction.contracts import InteractionPort
from mind_app.output.session import SessionFactory

ActivityStatusKind = typing.Literal[
    "wait",
    "upload",
    "download",
    "inbuild",
    "external_mcp",
]


@dataclass(frozen=True, slots=True)
class ApplicationView(object):
    """描述应用生命周期中的一项展示数据。"""

    type: str
    renderable: typing.Any = None
    end: str = "\n"
    payload: dict[str, typing.Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Viewport(object):
    """描述前端当前可用的展示尺寸。"""

    width: int | None = None
    height: int | None = None


class ApplicationSink(ABC):
    """接收跨单轮存在的应用级展示数据。"""

    @property
    @abstractmethod
    def viewport(self) -> Viewport:
        """返回当前前端展示尺寸。"""
        raise NotImplementedError

    @abstractmethod
    def emit(self, view: ApplicationView) -> None:
        """发送一项应用级展示数据。"""
        raise NotImplementedError


class FrontendRuntime(typing.Protocol):
    """描述交互前端的运行期生命周期和活动状态展示。"""

    @property
    def active(self) -> bool:
        """返回前端运行期是否正在接管终端。"""
        ...

    async def open(self) -> None:
        """启动交互前端运行期。"""
        ...

    async def close(self) -> None:
        """停止交互前端运行期。"""
        ...

    async def begin_wait_status(self) -> None:
        """显示覆盖当前交互周期的等待状态。"""
        ...

    async def begin_upload_status(
        self,
        snapshot: typing.Callable[[], dict[str, typing.Any]],
    ) -> None:
        """显示附件上传状态。"""
        ...

    async def begin_download_status(
        self,
        snapshot: typing.Callable[[], dict[str, typing.Any]],
    ) -> None:
        """显示运行时下载状态。"""
        ...

    async def begin_inbuild_status(
        self,
        snapshot: typing.Callable[[], dict[str, typing.Any]],
    ) -> None:
        """显示内置运行时启动状态。"""
        ...

    async def begin_external_mcp_status(
        self,
        snapshot: typing.Callable[[], dict[str, typing.Any]],
        *,
        persist_final: bool = False,
    ) -> None:
        """显示外部 MCP 启动状态。"""
        ...

    async def end_activity_status(
        self,
        kind: ActivityStatusKind | None = None,
    ) -> None:
        """结束当前活动状态。"""
        ...


class PassiveFrontendRuntime(object):
    """提供无需常驻前端运行期时的空实现。"""

    @property
    def active(self) -> bool:
        """返回未接管终端状态。"""
        return False

    async def open(self) -> None:
        """忽略启动请求。"""
        return None

    async def close(self) -> None:
        """忽略停止请求。"""
        return None

    async def begin_wait_status(self) -> None:
        """忽略等待状态请求。"""
        return None

    async def begin_upload_status(
        self,
        snapshot: typing.Callable[[], dict[str, typing.Any]],
    ) -> None:
        """忽略上传状态请求。"""
        _ = snapshot
        return None

    async def begin_download_status(
        self,
        snapshot: typing.Callable[[], dict[str, typing.Any]],
    ) -> None:
        """忽略运行时下载状态请求。"""
        _ = snapshot
        return None

    async def begin_inbuild_status(
        self,
        snapshot: typing.Callable[[], dict[str, typing.Any]],
    ) -> None:
        """忽略内置运行时状态请求。"""
        _ = snapshot
        return None

    async def begin_external_mcp_status(
        self,
        snapshot: typing.Callable[[], dict[str, typing.Any]],
        *,
        persist_final: bool = False,
    ) -> None:
        """忽略外部 MCP 状态请求。"""
        _ = snapshot, persist_final
        return None

    async def end_activity_status(
        self,
        kind: ActivityStatusKind | None = None,
    ) -> None:
        """忽略活动状态结束请求。"""
        _ = kind
        return None


@dataclass(frozen=True, slots=True)
class Frontend(object):
    """聚合应用级展示、交互和单轮输出装配能力。"""

    application: ApplicationSink
    interaction: InteractionPort
    session_factory: SessionFactory
    runtime: FrontendRuntime = field(default_factory=PassiveFrontendRuntime)


if __name__ == '__main__':
    pass
