# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace

import mind_app.modes.stream as stream_module
from mind_app.output import ContentOutput
from mind_app.output.session import OutputSession
from mind_app.presentation.approval_views import build_approval_view
from mind_app.presentation.contracts import PresentationView
from mind_app.presentation.legacy import LegacyPresentationSink
from mind_app.presentation.models import ApprovalView


class RecordingPresentationSink(object):
    """记录审批结果的结构化展示数据。"""

    def __init__(self, events: list[str] | None = None) -> None:
        self.views: list[PresentationView] = []
        self.events = events

    async def emit(self, view: PresentationView) -> None:
        self.views.append(view)
        if self.events is not None:
            self.events.append("emit")


class RecordingContentSink(object):
    """记录审批事件之后的正文输出。"""

    def __init__(self) -> None:
        self.outputs: list[ContentOutput] = []

    async def emit(self, output: ContentOutput) -> None:
        self.outputs.append(output)


class FakeOutput(object):
    """记录审批展示和流式生命周期行为。"""

    def __init__(self, events: list[str] | None = None) -> None:
        self.events = events
        self.feeds: list[tuple[str, dict]] = []
        self.blocks: list[tuple[str, dict]] = []
        self.end_calls: list[bool] = []
        self.wait_calls: list[tuple[float, float | None]] = []
        self.prepare_count = 0

    async def open(self) -> None:
        return None

    async def stop(self, *, blink: bool = True) -> None:
        _ = blink

    async def feed(self, text: str, **kwargs) -> None:
        self.feeds.append((text, kwargs))

    async def print_block(self, text: str, **kwargs) -> None:
        self.blocks.append((text, kwargs))

    async def prepare_external_output(self) -> None:
        self.prepare_count += 1

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


def approval_data() -> dict:
    """构造稳定的 shell 工具审批数据。"""
    return {
        "id": "approval-1",
        "tool": "shell_command",
        "arguments": {"command": "printf hello"},
    }


def test_approval_view_maps_all_decision_states() -> None:
    """审批 View 保留 decision 并映射三种展示状态。"""
    approval = approval_data()

    assert build_approval_view(approval, decision="accept") == ApprovalView(
        approval=approval,
        decision="accept",
        state="approved",
    )
    assert build_approval_view(approval, decision="acceptForSession").state == "approved"
    assert build_approval_view(approval, decision="decline").state == "denied"
    assert build_approval_view(approval, decision="expired").state == "expired"


def test_legacy_approval_view_keeps_direct_block_output() -> None:
    """默认审批适配器继续通过 print_block 立即落版。"""
    output = FakeOutput()
    presentation = LegacyPresentationSink(output)

    async def run() -> None:
        await presentation.emit(build_approval_view(
            approval_data(),
            decision="acceptForSession",
        ))
        await presentation.emit(build_approval_view(
            approval_data(),
            decision="decline",
        ))
        await presentation.emit(build_approval_view(
            approval_data(),
            decision="expired",
        ))

    asyncio.run(run())

    assert output.feeds == []
    assert len(output.blocks) == 3
    assert "for this session" in output.blocks[0][0]
    assert "You denied" in output.blocks[1][0]
    assert "Approval expired" in output.blocks[2][0]
    assert all(block[1]["display_parts"] for block in output.blocks)


def test_stream_approval_result_uses_injected_presentation(monkeypatch) -> None:
    """审批事件通过注入展示端输出并保持回填顺序。"""
    events: list[str] = []
    output = FakeOutput(events)
    presentation = RecordingPresentationSink(events)
    content = RecordingContentSink()
    posted: list[dict] = []

    class FakeMind(object):
        report = SimpleNamespace(log_papers="unused")
        level = "show"

        def is_service_mcp_linked(self) -> bool:
            return False

        async def stop_anim(self) -> None:
            return None

        def remember_last_assistant_reply(self, _text: str) -> None:
            return None

        async def await_cleanup(self, awaitable) -> None:
            await awaitable

    async def fake_stream_chat(*_args, **_kwargs):
        yield {
            "type": "tool.approval_required",
            "cid": "cid",
            "sid": "sid",
            "call_id": "call-1",
            "approval": approval_data(),
        }
        yield {"type": "turn.done"}

    async def fake_prompt(*_args, **_kwargs) -> str:
        events.append("prompt")
        return "decline"

    async def fake_post(*_args, **kwargs) -> None:
        posted.append(kwargs)
        events.append("post")

    monkeypatch.setattr(stream_module.request, "stream_chat", fake_stream_chat)
    monkeypatch.setattr(stream_module, "prompt_tool_approval_decision", fake_prompt)
    monkeypatch.setattr(stream_module.request, "post_tool_approval", fake_post)

    asyncio.run(stream_module.stream_looper(
        FakeMind(),
        SimpleNamespace(),
        "fast",
        {},
        "message",
        [],
        exec_env={},
        output_session_factory=lambda *_args, **_kwargs: OutputSession(
            control=output,
            content=content,
            presentation=presentation,
        ),
    ))

    assert isinstance(presentation.views[0], ApprovalView)
    assert presentation.views[0].decision == "decline"
    assert presentation.views[0].state == "denied"
    assert events[:4] == ["end", "prompt", "emit", "post"]
    assert posted == [{"decision": "decline", "reason": "user denied"}]
    assert output.blocks == []
    assert output.wait_calls == [(0.15, 0.85)]
    assert output.prepare_count == 1


if __name__ == '__main__':
    pass
