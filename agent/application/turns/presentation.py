# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import enum
import time
import typing

from agent.application.turns.stream_outcome import StreamTurnOutcome
from agent.application.views.builders.lifecycle import build_failure_view
from agent.application.views.builders.run import (
    build_run_completed_view,
    build_run_incomplete_view,
    build_run_started_view,
)
from agent.application.views.contracts import PresentationSink
from agent.domain.policies import PermissionSettings
from agent.ports import (
    ContentSink,
    EventReportPort,
    SourcesOutput,
)
from agent.ports import OutputStatusPort


class FailureProjectionMode(enum.Enum):
    """区分失败投影是否上报事件以及是否携带协议终态元数据。"""

    REPORTED = "reported"
    PROJECTION_ONLY = "projection_only"
    TERMINAL = "terminal"


class StreamTurnPresentation:
    """在单轮生命周期内投影启动、失败和最终运行状态。"""

    def __init__(
        self,
        *,
        outcome: StreamTurnOutcome,
        status_control: OutputStatusPort,
        content: ContentSink,
        presentation: PresentationSink,
        event_report: EventReportPort | None,
    ) -> None:
        """绑定单轮终态快照和与具体前端无关的输出端口。"""
        self._outcome = outcome
        self._status_control = status_control
        self._content = content
        self._presentation = presentation
        self._event_report = event_report

    async def emit_started(
        self,
        *,
        metadata: dict[str, typing.Any],
        message: str,
        pref_config: dict[str, typing.Any],
        workdir: str,
        permissions: PermissionSettings,
        turn_id: str,
        hook_warnings: typing.Iterable[str] = (),
    ) -> None:
        """投影当前轮次固定后的启动信息。"""
        await self._presentation.emit(build_run_started_view(
            metadata=metadata,
            message=message,
            pref_config=pref_config,
            workdir=workdir,
            permissions=permissions,
            turn_id=turn_id,
            hook_warnings=hook_warnings,
        ))

    async def emit_failure(
        self,
        phase: str,
        *,
        mode: FailureProjectionMode = FailureProjectionMode.REPORTED,
        effect_id: str | None = None,
    ) -> None:
        """结束失败状态并按指定模式投影错误和权威终态字段。"""
        message = "" if self._outcome.error is None else str(self._outcome.error)
        if mode is FailureProjectionMode.REPORTED:
            await self._report_failure(phase, message, effect_id=effect_id)

        await self._status_control.end_status(immediate=True)
        await self._presentation.emit(build_failure_view(
            phase,
            message,
            usage=(
                self._outcome.usage
                if mode is FailureProjectionMode.TERMINAL
                else None
            ),
            terminal_meta=(
                self._outcome.terminal_meta
                if mode is FailureProjectionMode.TERMINAL
                else None
            ),
        ))

    async def emit_result(self, sources: typing.Iterable[typing.Any]) -> None:
        """结束状态、投影来源，并按唯一终态输出完成或未完整视图。"""
        await self._status_control.end_status()
        await self._content.emit(SourcesOutput(tuple(sources)))

        if self._outcome.is_completed and not self._outcome.is_failed:
            await self._presentation.emit(build_run_completed_view(
                self._outcome.usage,
                self._outcome.terminal_meta,
            ))
        elif self._outcome.is_incomplete:
            await self._presentation.emit(build_run_incomplete_view(
                self._outcome.usage,
                reason=self._outcome.error,
                can_continue=self._outcome.can_continue,
                terminal_meta=self._outcome.terminal_meta,
            ))

    async def _report_failure(
        self,
        phase: str,
        message: str,
        *,
        effect_id: str | None = None,
    ) -> None:
        """把本地产生的失败事件写入报告队列并等待发送完成。"""
        if self._event_report is None:
            return
        event: dict[str, typing.Any] = {
            "type": phase,
            "ts": time.time(),
            "error": message,
        }
        if effect_id:
            event["effect_id"] = effect_id
        self._event_report.emit(event)
        await self._event_report.flush()


if __name__ == '__main__':
    pass
