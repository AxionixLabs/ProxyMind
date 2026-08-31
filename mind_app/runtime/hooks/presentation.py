# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from agent.application.views.contracts import PresentationSink
from agent.application.views import (
    HookOutputView,
    HookRunView,
    HookViewPhase
)
from agent.application.hooks.models import HookRunSummary


def build_hook_run_view(
    run: HookRunSummary,
    *,
    phase: HookViewPhase
) -> HookRunView:
    """把 Hook 运行快照转换为中立展示数据。"""
    return HookRunView(
        id=run.id,
        hook_key=run.hook_key,
        event=run.event,
        phase=phase,
        status=run.status,
        status_message=run.status_message,
        duration_ms=run.duration_ms,
        entries=tuple(
            HookOutputView(entry.kind, entry.text)
            for entry in run.entries
        ),
    )


class HookPresentationAdapter:
    """把 Hook 生命周期发送到单轮结构化展示通道。"""

    def __init__(self, sink: PresentationSink) -> None:
        self._sink = sink

    async def started(self, run: HookRunSummary) -> None:
        """发送 Hook 开始事件。"""
        await self._sink.emit(build_hook_run_view(run, phase="started"))

    async def completed(self, run: HookRunSummary) -> None:
        """发送 Hook 完成事件。"""
        await self._sink.emit(build_hook_run_view(run, phase="completed"))


if __name__ == '__main__':
    pass
