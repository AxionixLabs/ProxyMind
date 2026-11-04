# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import enum
import typing

TuiExitReason = typing.Literal[
    "interrupt",
    "eof"
]


class InterruptDisposition(enum.Enum):
    """描述一次 TUI 中断请求由当前控制面处理后的结果。"""
    IGNORED = "ignored"
    CONSUMED = "consumed"
    DRAFT_DISCARDED = "draft_discarded"
    EXIT_REQUESTED = "exit_requested"


class TuiInterruptState(object):
    """保存主输入区的显式退出请求及其来源。"""

    def __init__(self) -> None:
        self._exit_reason: TuiExitReason | None = None

    @property
    def exit_requested(self) -> bool:
        """返回当前是否已请求退出主交互循环。"""
        return self._exit_reason is not None

    def request_exit(self, reason: TuiExitReason = "interrupt") -> None:
        """记录退出请求。"""
        self._exit_reason = reason

    def consume_exit_request(self) -> TuiExitReason | None:
        """消费并返回当前退出请求。"""
        reason = self._exit_reason
        self._exit_reason = None
        return reason

    def clear(self) -> None:
        """清空退出状态。"""
        self._exit_reason = None


if __name__ == '__main__':
    pass
