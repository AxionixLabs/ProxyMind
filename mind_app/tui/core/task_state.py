# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing


class TuiTaskState(object):
    """聚合模型轮次和运行期活动来源的忙碌状态。"""

    def __init__(
        self,
        *,
        activity_running: typing.Callable[[], bool] = lambda: False
    ) -> None:
        self.turn_running = False
        self._activity_running = activity_running

    @property
    def running(self) -> bool:
        """返回当前是否存在模型轮次或运行期活动。"""
        return self.turn_running or self._activity_running()

    def set_turn_running(self, active: bool) -> None:
        """更新模型轮次运行状态。"""
        self.turn_running = bool(active)

    def clear(self) -> None:
        """清空模型轮次状态。"""
        self.turn_running = False


if __name__ == '__main__':
    pass
