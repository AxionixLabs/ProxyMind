# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass
from enum import Enum

from .text import FormattedLine


class ResumeFilterMode(str, Enum):
    """描述 Resume picker 的工作区过滤范围。"""
    CWD = "cwd"
    ALL = "all"


class ResumeSortKey(str, Enum):
    """描述 Resume picker 的会话排序字段。"""
    UPDATED = "updated"
    CREATED = "created"


class ResumeDensity(str, Enum):
    """描述 Resume picker 的列表信息密度。"""
    DENSE = "dense"
    COMFORTABLE = "comfortable"


class ResumeSessionStatus(str, Enum):
    """描述可恢复会话当前是否仍处于活动状态。"""
    ACTIVE = "active"
    ARCHIVED = "archived"


class ResumeArchiveStatus(str, Enum):
    """描述 picker 当前归档动作的生命周期。"""
    IDLE = "idle"
    PENDING = "pending"
    RESTORING = "restoring"


class ResumeLaunchContext(str, Enum):
    """描述 Resume picker 的退出语义来源。"""
    EXISTING_SESSION = "existing_session"


class ResumePreviewStatus(str, Enum):
    """描述 transcript preview 的异步加载状态。"""
    LOADING = "loading"
    READY = "ready"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ResumeRow(object):
    """保存一项可恢复会话的稳定展示快照。"""
    cid: str
    sid: str
    title: str
    workspace: str
    source: str
    created_at_ms: int | None
    updated_at_ms: int | None
    branch: str = ""
    status: ResumeSessionStatus = ResumeSessionStatus.ACTIVE

    @property
    def key(self) -> tuple[str, str]:
        """返回用于跨排序和异步结果匹配的稳定标识。"""
        return self.cid, self.sid

    def __post_init__(self) -> None:
        """把外部传入的 status 统一为 Resume 状态枚举。"""
        if isinstance(self.status, ResumeSessionStatus):
            return
        normalized = str(self.status or "").strip().casefold()
        object.__setattr__(
            self,
            "status",
            (
                ResumeSessionStatus.ARCHIVED
                if normalized == ResumeSessionStatus.ARCHIVED.value
                else ResumeSessionStatus.ACTIVE
            ),
        )


@dataclass(frozen=True, slots=True)
class ResumePreview(object):
    """保存指定会话 transcript preview 的一次加载结果。"""
    row_key: tuple[str, str]
    status: ResumePreviewStatus
    blocks: tuple[FormattedLine, ...] = ()
    error: str | None = None


class ResumePreviewLoader(typing.Protocol):
    """定义 picker 生命周期内按需读取 transcript preview 的能力。"""

    async def load(
        self,
        row: ResumeRow,
        *,
        width: int,
    ) -> ResumePreview:
        """读取指定行在当前显示宽度下的 transcript preview。"""
        ...


class ResumeTranscriptLoader(typing.Protocol):
    """定义打开全屏 transcript pager 时读取完整内容的能力。"""

    async def load(
        self,
        row: ResumeRow,
        *,
        width: int,
    ) -> ResumePreview:
        """读取指定会话的完整富文本 transcript。"""
        ...


@dataclass(frozen=True, slots=True)
class ResumePickerRequest(object):
    """描述一次独立 Resume picker 会话的完整输入快照。"""
    rows: tuple[ResumeRow, ...]
    filter_workspace: str | None = None
    show_workspace: bool = False
    initial_filter: ResumeFilterMode = ResumeFilterMode.CWD
    initial_status: ResumeSessionStatus = ResumeSessionStatus.ACTIVE
    initial_sort: ResumeSortKey = ResumeSortKey.UPDATED
    initial_density: ResumeDensity = ResumeDensity.DENSE
    launch_context: ResumeLaunchContext = ResumeLaunchContext.EXISTING_SESSION
    preview_loader: ResumePreviewLoader | None = None
    transcript_loader: ResumeTranscriptLoader | None = None
    archive_session: typing.Callable[[ResumeRow], typing.Awaitable[None]] | None = None
    unarchive_session: typing.Callable[[ResumeRow], typing.Awaitable[ResumeRow]] | None = None


ResumePickerResult: typing.TypeAlias = ResumeRow | None


if __name__ == '__main__':
    pass
