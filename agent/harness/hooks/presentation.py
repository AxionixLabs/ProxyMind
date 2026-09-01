# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from agent.application.views.contracts import PresentationSink
from agent.application.hooks.models import HookRunSummary
from agent.application.views.builders.hooks import build_hook_run_view


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
