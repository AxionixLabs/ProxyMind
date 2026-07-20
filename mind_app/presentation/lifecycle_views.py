# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from .models import (
    FailureView,
    LifecycleView
)


def build_failure_view(phase: str, error: typing.Any) -> FailureView:
    """构建运行失败的结构化展示数据。"""
    normalized_phase = str(phase or "stream.failed").strip() or "stream.failed"
    message          = "" if error is None else str(error)

    return FailureView(phase=normalized_phase, error=message)


def build_lifecycle_view(text: typing.Any) -> LifecycleView | None:
    """构建生命周期事件的结构化展示数据。"""
    normalized_text = str(text or "").strip()
    if not normalized_text:
        return None

    return LifecycleView(text=normalized_text)


if __name__ == '__main__':
    pass
