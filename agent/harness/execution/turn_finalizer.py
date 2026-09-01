# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from observability import observe_exception
from agent.ports.transcript import TranscriptLifecyclePort
from agent.ports import (
    IdleStatusPort,
    OutputControlPort,
)
from agent.application.turns.transcript import record_turn_finished
from agent.application.turns.stream_outcome import StreamTurnOutcome
from agent.application.hooks.models import StopHookDecision
from agent.harness.hooks.turn_lifecycle import TurnHookEvents


class _TurnStateStore(typing.Protocol):
    """定义单轮临时状态清理端口；实现方必须同步、幂等且只清除给定身份。"""

    def clear_turn(self, *, cid: str, sid: str, turn_id: str) -> None:
        """清除给定逻辑轮次拥有的临时状态。"""
        ...


class _ModelOutputLifecycle(typing.Protocol):
    """定义单轮模型展示记录的收尾端口。"""

    def flush_pending(self, *, complete_only: bool = False) -> None:
        """提交尚未写入会话记录的模型正文。"""
        ...


_AwaitCleanup = typing.Callable[
    [typing.Awaitable[typing.Any]],
    typing.Awaitable[typing.Any],
]


class StreamTurnFinalizer:
    """作为单轮唯一收尾者清理临时状态、记录终态并运行停止 Hook。"""

    def __init__(
        self,
        *,
        cid: str,
        sid: str,
        turn_id: str,
        outcome: StreamTurnOutcome,
        turn_state_stores: typing.Iterable[_TurnStateStore],
        transcript: TranscriptLifecyclePort,
        model_output: _ModelOutputLifecycle,
        retry_state_close: typing.Callable[[], None],
        stream_end: typing.Callable[[str], None] | None,
        idle_wait: IdleStatusPort,
        output_control: OutputControlPort,
        await_cleanup: _AwaitCleanup,
        continuation_count: int,
    ) -> None:
        """绑定当前轮次的资源、终态状态和续跑计数。"""
        self._cid = str(cid)
        self._sid = str(sid)
        self._turn_id = str(turn_id)
        self._outcome = outcome
        self._turn_state_stores = tuple(turn_state_stores)
        self._transcript = transcript
        self._model_output = model_output
        self._retry_state_close = retry_state_close
        self._stream_end = stream_end
        self._idle_wait = idle_wait
        self._output_control = output_control
        self._await_cleanup = await_cleanup
        self._continuation_count = int(continuation_count)

    async def finalize(
        self,
        *,
        stream_end_reason: str | None,
        hook_events: TurnHookEvents | None,
        prompt_blocked: bool,
        assistant_text: str,
    ) -> StopHookDecision:
        """按既定顺序收束当前轮次并返回可选的停止 Hook 续跑决定。"""
        self._clear_turn_state()
        self._retry_state_close()

        if self._stream_end is not None and stream_end_reason is not None:
            self._stream_end(stream_end_reason or "cancelled")

        self._model_output.flush_pending()
        record_turn_finished(
            self._transcript,
            status=self._outcome.status,
            usage=self._outcome.usage,
            error=self._outcome.error,
            terminal_meta=self._outcome.terminal_meta,
        )

        stop_decision = await self._run_stop_hook(
            hook_events,
            prompt_blocked=prompt_blocked,
            assistant_text=assistant_text,
        )

        self._transcript.close()
        await self._idle_wait.cancel()
        await self._await_cleanup(self._output_control.stop(
            blink=not self._outcome.is_interrupted,
        ))
        return stop_decision

    def _clear_turn_state(self) -> None:
        """清除所有绑定到当前逻辑轮次的临时状态。"""
        for store in self._turn_state_stores:
            store.clear_turn(
                cid=self._cid,
                sid=self._sid,
                turn_id=self._turn_id,
            )

    async def _run_stop_hook(
        self,
        hook_events: TurnHookEvents | None,
        *,
        prompt_blocked: bool,
        assistant_text: str,
    ) -> StopHookDecision:
        """执行允许的停止 Hook，并隔离其失败和中断态续跑决定。"""
        stop_decision = StopHookDecision.stop()
        if hook_events is None or prompt_blocked:
            return stop_decision

        try:
            stop_hook = hook_events.stop(
                outcome=self._outcome.status,
                error=self._outcome.error,
                usage=self._outcome.usage,
                last_assistant_message=assistant_text,
                continuation_count=self._continuation_count,
            )
            if self._outcome.is_interrupted:
                await self._await_cleanup(_discard_stop_hook_decision(stop_hook))
            else:
                stop_decision = await stop_hook
        except Exception as error:
            observe_exception(
                "hooks.stop.failed",
                error,
                level="WARNING",
                turn_id=self._turn_id,
            )
        return stop_decision


async def _discard_stop_hook_decision(
    awaitable: typing.Awaitable[StopHookDecision],
) -> None:
    """执行停止 Hook 并丢弃清理阶段不应消费的续跑决定。"""
    await awaitable


if __name__ == '__main__':
    pass
