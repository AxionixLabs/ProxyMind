# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import copy
import typing
from .models import (
    FailureView,
    LifecycleView
)


def build_failure_view(
    phase: str,
    error: typing.Any,
    *,
    usage: dict[str, typing.Any] | None = None,
    terminal_meta: typing.Mapping[str, typing.Any] | None = None
) -> FailureView:
    """构建运行失败的结构化展示数据。"""
    normalized_phase = str(phase or "stream.failed").strip() or "stream.failed"
    message          = "" if error is None else str(error)
    meta             = dict(terminal_meta or {})

    return FailureView(
        phase=normalized_phase,
        error=message,
        usage=copy.deepcopy(usage) if isinstance(usage, dict) else {},
        response_id=str(meta.get("response_id") or ""),
        model=str(meta.get("model") or ""),
        route=str(meta.get("route") or ""),
        request_id=str(meta.get("request_id") or ""),
        service_tier=str(meta.get("service_tier") or ""),
        stop_reason=meta.get("stop_reason"),
        stop_sequence=meta.get("stop_sequence"),
    )


def build_lifecycle_view(text: typing.Any) -> LifecycleView | None:
    """构建生命周期事件的结构化展示数据。"""
    normalized_text = str(text or "").strip()
    if not normalized_text:
        return None

    return LifecycleView(text=normalized_text)


if __name__ == '__main__':
    pass
