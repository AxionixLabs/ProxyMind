# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from .models import (
    RunCompletedView,
    RunStartedView
)


def build_run_started_view(
    *,
    metadata: dict[str, typing.Any],
    message: str,
    mode: str,
    pref_config: dict[str, typing.Any],
    workdir: str,
    sandbox: str,
    turn_id: str,
) -> RunStartedView:
    """构建一次运行的启动展示数据。"""
    primary = pref_config.get("primary") if isinstance(pref_config, dict) else None
    model   = primary.get("model") if isinstance(primary, dict) else ""

    return RunStartedView(
        thread_id=str(metadata.get("cid") or metadata.get("sid") or ""),
        turn_id=str(turn_id or ""),
        message=str(message or ""),
        mode=str(mode or ""),
        model=str(model or ""),
        workdir=str(workdir or ""),
        sandbox=str(sandbox or ""),
    )


def build_run_completed_view(
    usage: dict[str, typing.Any] | None,
) -> RunCompletedView:
    """构建一次运行的完成展示数据。"""
    return RunCompletedView(usage=dict(usage) if isinstance(usage, dict) else {})


if __name__ == '__main__':
    pass
