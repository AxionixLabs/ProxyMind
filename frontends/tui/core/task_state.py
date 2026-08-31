# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import enum


class TurnPhase(enum.Enum):
    """描述单轮模型生命周期的阶段。"""
    IDLE = "idle"
    PENDING = "pending"
    RUNNING = "running"
    FINISHING = "finishing"


class TuiTaskState(object):
    """聚合模型轮次和运行期活动来源的忙碌状态。"""

    def __init__(
        self,
        *,
        activity_running: typing.Callable[[], bool] = lambda: False
    ) -> None:
        self.turn_phase         = TurnPhase.IDLE
        self.foreground_running = False
        self._activity_running   = activity_running

    @property
    def turn_running(self) -> bool:
        """返回模型执行体是否正在运行。"""
        return self.turn_phase is TurnPhase.RUNNING

    @property
    def turn_start_pending(self) -> bool:
        """返回模型轮次是否等待进入执行态。"""
        return self.turn_phase is TurnPhase.PENDING

    @property
    def turn_active(self) -> bool:
        """返回当前回合是否仍处于生命周期内。"""
        return self.turn_phase is not TurnPhase.IDLE

    @property
    def turn_finishing(self) -> bool:
        """返回当前回合是否正在清理等待状态。"""
        return self.turn_phase is TurnPhase.FINISHING

    @property
    def turn_wait_active(self) -> bool:
        """返回当前回合是否仍允许活动状态接管等待区域。"""
        return self.turn_phase in {
            TurnPhase.PENDING,
            TurnPhase.RUNNING,
        }

    @property
    def running(self) -> bool:
        """返回当前是否存在模型轮次或运行期活动。"""
        return (
            self.turn_active
            or self.foreground_running
            or self._activity_running()
        )

    def set_turn_running(self, active: bool) -> None:
        """更新模型轮次运行状态。"""
        if active:
            self.turn_phase = TurnPhase.RUNNING
        elif self.turn_phase in {
            TurnPhase.PENDING,
            TurnPhase.RUNNING,
            TurnPhase.FINISHING,
        }:
            self.turn_phase = TurnPhase.IDLE

    def set_turn_start_pending(self, pending: bool) -> None:
        """更新已提交但尚未进入执行态的模型轮次状态。"""
        if pending:
            if self.turn_phase is TurnPhase.IDLE:
                self.turn_phase = TurnPhase.PENDING
        elif self.turn_phase is TurnPhase.PENDING:
            self.turn_phase = TurnPhase.IDLE

    def finish_turn_wait(self) -> None:
        """结束模型轮次等待状态的生命周期所有权。"""
        if self.turn_phase in {TurnPhase.PENDING, TurnPhase.RUNNING}:
            self.turn_phase = TurnPhase.FINISHING

    def set_foreground_running(self, active: bool) -> None:
        """更新前台屏障等待状态。"""
        self.foreground_running = bool(active)

    def clear(self) -> None:
        """清空模型轮次状态。"""
        self.turn_phase         = TurnPhase.IDLE
        self.foreground_running = False


if __name__ == '__main__':
    pass
