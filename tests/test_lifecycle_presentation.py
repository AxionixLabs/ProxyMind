# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace

from mind_app.presentation.lifecycle_views import (
    build_failure_view,
    build_lifecycle_view,
)
from mind_app.presentation.models import FailureView, LifecycleView
from mind_app.presentation.rich import (
    render_failure_view,
    render_lifecycle_view,
)
from mind_app.runtime.support import loop_support
from mind_app.stream_events.lifecycle import (
    StreamEventContext,
    handle_lifecycle_event,
)


class RecordingPresentationSink(object):
    """记录生命周期发送的结构化展示数据。"""

    def __init__(self, events: list | None = None) -> None:
        self.views: list[FailureView | LifecycleView] = []
        self.events = events

    async def emit(self, view: FailureView | LifecycleView) -> None:
        self.views.append(view)
        if self.events is not None:
            self.events.append("emit")


class FakeOutput(object):
    """记录生命周期保留的状态控制行为。"""

    def __init__(self, events: list | None = None) -> None:
        self.events = events
        self.end_calls: list[bool] = []
        self.wait_calls: list[tuple[float, float | None]] = []

    async def end_status(self, *, immediate: bool = False) -> None:
        self.end_calls.append(immediate)
        if self.events is not None:
            self.events.append("end")

    async def begin_reply_wait_status(
        self,
        _text: str | None = "thinking",
        *,
        delay_sec: float = 0.28,
        animate_after_sec: float | None = None,
    ) -> None:
        self.wait_calls.append((delay_sec, animate_after_sec))


def test_lifecycle_views_keep_legacy_rendering() -> None:
    """生命周期 View 保持现有失败摘要和普通标题展示。"""
    failure = build_failure_view("turn.failed", "first line\nsecond line")
    lifecycle = build_lifecycle_view("  preparing result  ")

    assert failure == FailureView(
        phase="turn.failed",
        error="first line\nsecond line",
    )
    assert lifecycle == LifecycleView(text="preparing result")

    failure_block = render_failure_view(failure)
    lifecycle_block = render_lifecycle_view(lifecycle)

    assert failure_block.text == "■ turn.failed\n└ first line ... (+1 lines)"
    assert failure_block.preserve_display_parts is True
    assert lifecycle_block.text == "• preparing result"
    assert lifecycle_block.preserve_display_parts is False


def test_finish_failure_keeps_report_status_and_display_order(monkeypatch) -> None:
    """失败处理保持上报、状态收束和展示顺序。"""
    events: list[str] = []
    output = FakeOutput(events)
    presentation = RecordingPresentationSink(events)
    report = object()
    finish_args: list[tuple[object, str, str]] = []

    async def fake_finish_stream(
        actual_report,
        *,
        phase: str,
        error: str,
        **_extra,
    ) -> None:
        finish_args.append((actual_report, phase, error))
        events.append("finish")

    monkeypatch.setattr(loop_support, "finish_stream", fake_finish_stream)

    asyncio.run(loop_support.finish_failure(
        output,
        presentation,
        report,
        phase="turn.failed",
        error="network down",
    ))

    assert events == ["finish", "end", "emit"]
    assert finish_args == [(report, "turn.failed", "network down")]
    assert output.end_calls == [True]
    assert presentation.views == [FailureView(
        phase="turn.failed",
        error="network down",
    )]


def test_lifecycle_display_uses_presentation_and_keeps_wait_status() -> None:
    """通用生命周期展示发送 View 后继续启动等待状态。"""
    output = FakeOutput()
    presentation = RecordingPresentationSink()
    context = StreamEventContext(
        mind=SimpleNamespace(),
        session=SimpleNamespace(),
        slog=output,
        presentation=presentation,
        tracker=SimpleNamespace(),
        mode="fast",
        pref_config={},
        metadata={},
    )

    handled = asyncio.run(handle_lifecycle_event(
        "custom.event",
        {"display": {"message": " preparing result "}},
        context,
    ))

    assert handled is True
    assert presentation.views == [LifecycleView(text="preparing result")]
    assert output.wait_calls == [(0.15, 0.85)]


if __name__ == '__main__':
    pass
