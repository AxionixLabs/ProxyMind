# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from agent.application.hooks.models import HookRunSummary
from agent.application.views import (
    HookOutputView,
    HookRunView,
    HookViewPhase,
)

__all__ = ("build_hook_run_view",)


def build_hook_run_view(
    run: HookRunSummary,
    *,
    phase: HookViewPhase,
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


if __name__ == '__main__':
    pass
