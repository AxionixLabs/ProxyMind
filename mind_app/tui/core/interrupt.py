# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing

TuiExitReason = typing.Literal["interrupt", "eof"]


class TuiInterruptState(object):
    """管理主输入区的连续取消、轮次中断和退出请求。"""

    def __init__(
        self,
        *,
        timeout_sec: float = 2.0,
        clock: typing.Callable[[], float] = time.monotonic,
    ) -> None:
        self.timeout_sec = max(0.1, float(timeout_sec))
        self._clock = clock
        self._armed_until = 0.0
        self._turn_interrupt_requested = False
        self._exit_reason: TuiExitReason | None = None

    @property
    def exit_armed(self) -> bool:
        """返回当前是否仍处于连续按键退出确认窗口。"""
        return self._armed_until > self._clock()

    @property
    def exit_requested(self) -> bool:
        """返回当前是否已请求退出主交互循环。"""
        return self._exit_reason is not None

    def arm_exit(self) -> None:
        """开启连续按键退出确认窗口。"""
        self._armed_until = self._clock() + self.timeout_sec

    def disarm_exit(self) -> None:
        """关闭连续按键退出确认窗口。"""
        self._armed_until = 0.0

    def request_exit(self, reason: TuiExitReason = "interrupt") -> None:
        """记录退出请求并关闭确认窗口。"""
        self._exit_reason = reason
        self.disarm_exit()

    def consume_exit_request(self) -> TuiExitReason | None:
        """消费并返回当前退出请求。"""
        reason = self._exit_reason
        self._exit_reason = None
        return reason

    def request_turn_interrupt(self) -> None:
        """记录由用户发起的当前轮次中断。"""
        self._turn_interrupt_requested = True

    def consume_turn_interrupt(self) -> bool:
        """消费并返回当前轮次的用户中断标记。"""
        requested = self._turn_interrupt_requested
        self._turn_interrupt_requested = False
        return requested

    def clear(self) -> None:
        """清空全部中断和退出状态。"""
        self._armed_until = 0.0
        self._turn_interrupt_requested = False
        self._exit_reason = None


if __name__ == '__main__':
    pass
