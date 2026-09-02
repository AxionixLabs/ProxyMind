# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import copy
import re
import typing

from agent.application.views import (
    RunCompletedView,
    RunIncompleteView,
    RunStartedView,
)
from agent.domain.policies import PermissionSettings


def _display_workdir(value: typing.Any) -> str:
    """返回适合终端展示的工作区路径。"""
    workdir = str(value or "")
    if re.match(r"^[a-zA-Z]:[\\/]", workdir):
        return workdir[0].upper() + workdir[1:]
    return workdir


def build_run_started_view(
    *,
    metadata: dict[str, typing.Any],
    message: str,
    pref_config: dict[str, typing.Any],
    workdir: str,
    permissions: PermissionSettings,
    turn_id: str,
    hook_warnings: typing.Iterable[str] = (),
) -> RunStartedView:
    """构建一次运行的启动展示数据。"""
    primary = pref_config.get("primary") if isinstance(pref_config, dict) else None
    primary = primary if isinstance(primary, dict) else {}
    model = primary.get("model")

    provider = str(
        primary.get("name")
        or primary.get("provider")
        or "-"
    )

    return RunStartedView(
        thread_id=str(metadata.get("cid") or metadata.get("sid") or ""),
        turn_id=str(turn_id or ""),
        session_id=str(metadata.get("sid") or metadata.get("cid") or ""),
        message=str(message or ""),
        model=str(model or ""),
        provider=provider,
        approval=permissions.approval_policy,
        workdir=_display_workdir(workdir),
        sandbox=permissions.sandbox_mode,
        reasoning_effort=str(primary.get("reasoning_effort") or "none"),
        reasoning_summaries=str(
            primary.get("reasoning_summaries")
            or primary.get("reasoning_summary")
            or "none"
        ),
        hook_warnings=tuple(
            str(warning)
            for warning in hook_warnings
            if str(warning)
        ),
    )


def build_run_completed_view(
    usage: dict[str, typing.Any] | None,
    terminal_meta: dict[str, typing.Any] | None = None
) -> RunCompletedView:
    """构建一次运行的完成展示数据。"""
    meta = terminal_meta if isinstance(terminal_meta, dict) else {}

    return RunCompletedView(
        usage=copy.deepcopy(usage) if isinstance(usage, dict) else {},
        response_id=str(meta.get("response_id") or ""),
        model=str(meta.get("model") or ""),
        route=str(meta.get("route") or ""),
        request_id=str(meta.get("request_id") or ""),
        service_tier=str(meta.get("service_tier") or ""),
        stop_reason=meta.get("stop_reason"),
        stop_sequence=meta.get("stop_sequence"),
    )


def build_run_incomplete_view(
    usage: dict[str, typing.Any] | None,
    *,
    reason: str | None = None,
    can_continue: bool | None = None,
    terminal_meta: dict[str, typing.Any] | None = None
) -> RunIncompleteView:
    """构建一次未完整结束的运行展示数据。"""
    meta = terminal_meta if isinstance(terminal_meta, dict) else {}

    return RunIncompleteView(
        usage=copy.deepcopy(usage) if isinstance(usage, dict) else {},
        reason=str(reason or meta.get("reason") or ""),
        can_continue=(
            can_continue
            if can_continue is not None
            else meta.get("can_continue")
        ),
        response_id=str(meta.get("response_id") or ""),
        model=str(meta.get("model") or ""),
        route=str(meta.get("route") or ""),
        request_id=str(meta.get("request_id") or ""),
        service_tier=str(meta.get("service_tier") or ""),
        stop_reason=meta.get("stop_reason"),
        stop_sequence=meta.get("stop_sequence"),
    )


if __name__ == '__main__':
    pass
