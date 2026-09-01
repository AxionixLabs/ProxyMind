# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from abc import (
    ABC,
    abstractmethod,
)
from .resume import (
    ResumePickerRequest,
    ResumePickerResult,
    ResumePreview,
)
from .transcript import MailboxEntry


class MailboxScreenPort(ABC):
    """描述 Screen 提供的 mailbox 快照和详情画面能力。"""

    @abstractmethod
    def set_mailbox_entries(
        self,
        entries: typing.Iterable[MailboxEntry],
        *,
        listener_active: bool,
    ) -> bool:
        """更新当前 Screen 的 mailbox 快照并返回是否变化。"""
        raise NotImplementedError

    @abstractmethod
    def set_mailbox_overlay(
        self,
        active: bool,
        *,
        entry_key: str | None = None,
        allow_menu: bool = False,
    ) -> bool:
        """切换当前 Screen 的 mailbox overlay 并返回是否发生变化。"""
        raise NotImplementedError


class ResumePickerScreenPort(ABC):
    """描述 Screen 提供的 Resume picker 生命周期能力。"""

    @abstractmethod
    def set_resume_picker(
        self,
        active: bool,
        *,
        request: ResumePickerRequest | None = None,
        generation: int = 0,
    ) -> bool:
        """切换 Resume picker 画面并返回是否发生变化。"""
        raise NotImplementedError

    @abstractmethod
    async def wait_resume_picker(self) -> ResumePickerResult:
        """等待当前 Resume picker 返回选择或取消。"""
        raise NotImplementedError

    @abstractmethod
    def set_resume_preview(
        self,
        preview: ResumePreview,
        *,
        generation: int,
    ) -> bool:
        """提交属于当前 picker generation 的 preview 结果。"""
        raise NotImplementedError


if __name__ == '__main__':
    pass
