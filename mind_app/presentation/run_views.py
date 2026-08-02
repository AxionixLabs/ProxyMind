# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import typing
from mind_core.provider_config import SUPPORTED_PROVIDER_OPTIONS
from mind_core.permissions import PermissionSettings
from .models import (
    RunCompletedView,
    RunStartedView
)

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
    turn_id: str
) -> RunStartedView:
    """构建一次运行的启动展示数据。"""
    primary  = pref_config.get("primary") if isinstance(pref_config, dict) else None
    primary  = primary if isinstance(primary, dict) else {}
    model    = primary.get("model")
    provider = _provider_label(primary.get("provider"))

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
    )


def _provider_label(value: typing.Any) -> str:
    """返回配置中的 provider 展示名称。"""
    normalized = str(value or "").strip()
    for option in SUPPORTED_PROVIDER_OPTIONS:
        if option.get("value") == normalized:
            return str(option.get("label") or normalized)
    return normalized or "-"


def build_run_completed_view(
    usage: dict[str, typing.Any] | None
) -> RunCompletedView:
    """构建一次运行的完成展示数据。"""
    return RunCompletedView(usage=dict(usage) if isinstance(usage, dict) else {})


if __name__ == '__main__':
    pass
