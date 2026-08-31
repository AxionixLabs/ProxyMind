# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import enum
import typing
from dataclasses import dataclass

ENTER_QUEUE_DEPTH_ROWS: typing.Final[int]      = 8
ENTER_OLDEST_AGE_SEC: typing.Final[float]      = 0.12
EXIT_QUEUE_DEPTH_ROWS: typing.Final[int]       = 2
EXIT_OLDEST_AGE_SEC: typing.Final[float]       = 0.04
EXIT_HOLD_SEC: typing.Final[float]             = 0.25
REENTER_CATCH_UP_HOLD_SEC: typing.Final[float] = 0.25
SEVERE_QUEUE_DEPTH_ROWS: typing.Final[int]     = 64
SEVERE_OLDEST_AGE_SEC: typing.Final[float]     = 0.30


class StreamChunkingMode(enum.Enum):
    """描述流式显示队列当前采用的释放节奏。"""
    SMOOTH = "smooth"
    CATCH_UP = "catch_up"


@dataclass(frozen=True, slots=True)
class StreamQueueSnapshot(object):
    """保存一次决策所需的待显示行数和最老行等待时间。"""
    pending_rows: int
    oldest_age_sec: float | None


@dataclass(frozen=True, slots=True)
class StreamDrainDecision(object):
    """描述一次流式显示 tick 应释放的行数和决策后模式。"""
    mode: StreamChunkingMode
    row_count: int
    entered_catch_up: bool


class StreamChunkingPolicy(object):
    """根据显示队列压力维护平滑和追赶模式的滞回状态。"""

    def __init__(self) -> None:
        self._mode = StreamChunkingMode.SMOOTH
        self._below_exit_threshold_since: float | None = None
        self._last_catch_up_exit_at: float | None = None

    @property
    def mode(self) -> StreamChunkingMode:
        """返回最近一次决策后的释放模式。"""
        return self._mode

    @staticmethod
    def _should_enter(
        *,
        pending_rows: int,
        oldest_age_sec: float | None
    ) -> bool:
        """返回当前压力是否达到进入追赶模式的阈值。"""
        return bool(
            pending_rows >= ENTER_QUEUE_DEPTH_ROWS
            or (
                oldest_age_sec is not None
                and oldest_age_sec >= ENTER_OLDEST_AGE_SEC
            )
        )

    @staticmethod
    def _should_exit(
        *,
        pending_rows: int,
        oldest_age_sec: float | None
    ) -> bool:
        """返回当前压力是否足够低，可以开始退出保持。"""
        return bool(
            pending_rows <= EXIT_QUEUE_DEPTH_ROWS
            and oldest_age_sec is not None
            and oldest_age_sec <= EXIT_OLDEST_AGE_SEC
        )

    @staticmethod
    def _is_severe(
        *,
        pending_rows: int,
        oldest_age_sec: float | None
    ) -> bool:
        """返回积压是否严重到应绕过重入冷却。"""
        return bool(
            pending_rows >= SEVERE_QUEUE_DEPTH_ROWS
            or (
                oldest_age_sec is not None
                and oldest_age_sec >= SEVERE_OLDEST_AGE_SEC
            )
        )

    def _maybe_exit(
        self,
        *,
        pending_rows: int,
        oldest_age_sec: float | None,
        now: float
    ) -> None:
        """在低压状态持续满足保持时间后退出追赶模式。"""
        if not self._should_exit(
            pending_rows=pending_rows,
            oldest_age_sec=oldest_age_sec,
        ):
            self._below_exit_threshold_since = None
            return None

        since = self._below_exit_threshold_since
        if since is None:
            self._below_exit_threshold_since = now
            return None
        if max(0.0, now - since) < EXIT_HOLD_SEC:
            return None

        self._mode = StreamChunkingMode.SMOOTH
        self._below_exit_threshold_since = None
        self._last_catch_up_exit_at = now

    def _reentry_hold_active(self, *, now: float) -> bool:
        """返回最近退出后的重入冷却是否仍生效。"""
        exited_at = self._last_catch_up_exit_at
        return bool(
            exited_at is not None
            and max(0.0, now - exited_at) < REENTER_CATCH_UP_HOLD_SEC
        )

    def reset(self) -> None:
        """恢复平滑模式并清除跨流的滞回时间。"""
        self._mode = StreamChunkingMode.SMOOTH
        self._below_exit_threshold_since = None
        self._last_catch_up_exit_at = None

    def decide(
        self,
        snapshot: StreamQueueSnapshot,
        *,
        now: float,
    ) -> StreamDrainDecision:
        """根据当前队列快照返回本次 tick 的显示行数。"""
        pending_rows = max(0, int(snapshot.pending_rows))

        oldest_age_sec = (
            None
            if snapshot.oldest_age_sec is None
            else max(0.0, float(snapshot.oldest_age_sec))
        )

        if pending_rows == 0:
            if self._mode is StreamChunkingMode.CATCH_UP:
                self._last_catch_up_exit_at = float(now)
            self._mode = StreamChunkingMode.SMOOTH
            self._below_exit_threshold_since = None
            return StreamDrainDecision(
                mode=self._mode,
                row_count=0,
                entered_catch_up=False,
            )

        entered_catch_up = False
        if self._mode is StreamChunkingMode.SMOOTH:
            if self._should_enter(
                pending_rows=pending_rows,
                oldest_age_sec=oldest_age_sec,
            ) and (
                not self._reentry_hold_active(now=float(now))
                or self._is_severe(
                pending_rows=pending_rows,
                oldest_age_sec=oldest_age_sec,
            )
            ):
                self._mode = StreamChunkingMode.CATCH_UP
                self._below_exit_threshold_since = None
                self._last_catch_up_exit_at = None
                entered_catch_up = True
        else:
            self._maybe_exit(
                pending_rows=pending_rows,
                oldest_age_sec=oldest_age_sec,
                now=float(now),
            )

        row_count = (
            pending_rows
            if self._mode is StreamChunkingMode.CATCH_UP
            else 1
        )
        return StreamDrainDecision(
            mode=self._mode,
            row_count=row_count,
            entered_catch_up=entered_catch_up,
        )


if __name__ == '__main__':
    pass
