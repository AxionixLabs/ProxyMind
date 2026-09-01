# -*- coding: utf-8 -*-

from collections.abc import Callable

from agent.ports import RetryState


class RetryStatus:
    """合并传输重连与 provider 重试状态并通知展示端。"""

    def __init__(
        self,
        sink: Callable[[RetryState], None] | None,
    ) -> None:
        """绑定状态出口并初始化两个独立重试来源。"""
        self._sink = sink
        self._transport = False
        self._provider = False
        self._state: RetryState = "idle"

    def set_transport(self, retrying: bool) -> None:
        """更新事件传输重连状态。"""
        self._transport = bool(retrying)
        self._refresh()

    def set_provider(self, retrying: bool) -> None:
        """更新供应商流重试状态。"""
        self._provider = bool(retrying)
        self._refresh()

    def close(self) -> None:
        """清除所有重试来源并结束可见状态。"""
        self._transport = False
        self._provider = False
        self._refresh()

    def _refresh(self) -> None:
        """按传输优先级合并重试来源并通知展示端。"""
        state: RetryState = (
            "transport"
            if self._transport
            else "provider"
            if self._provider
            else "idle"
        )
        if state == self._state:
            return
        self._state = state
        if self._sink is not None:
            self._sink(state)


__all__ = ("RetryStatus",)
