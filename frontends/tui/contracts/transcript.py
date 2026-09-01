# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import (
    dataclass,
    field,
)
from pathlib import Path

TranscriptExportFormat: typing.TypeAlias = typing.Literal[
    "markdown",
    "raw",
]


@dataclass(frozen=True, slots=True)
class TranscriptExportResult(object):
    """描述记录导出回调返回的结构化结果。"""
    path: Path
    format: TranscriptExportFormat
    cell_count: int


@dataclass(frozen=True, slots=True)
class TranscriptBacktrackRequest(object):
    """描述从完整记录中重新编辑一条用户输入的请求。"""
    turn_id: str
    prompt: str
    attachments: tuple[dict[str, typing.Any], ...] = ()
    extras: dict[str, typing.Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MailboxEntry(object):
    """描述全屏收件箱中的一条只读消息。"""
    key: str
    title: str
    message: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class MailboxRunRequest(object):
    """描述需要由 TUI 主循环串行执行的收件箱消息。"""
    message_id: str
    automatic: bool = False


if __name__ == '__main__':
    pass
