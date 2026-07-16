# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import mind_app.modes.stream as stream_module
from mind_app.stream_ui import StreamUI


class FakeTextState(object):
    """提供外部输出边界测试所需的最小文本状态。"""

    def __init__(self, display_text: str = "") -> None:
        self.display_text = display_text
        self.external_boundary = None
        self.spacings: list[tuple[str, str]] = []

    def remember_external_spacing(self, *, display: str, text: str) -> None:
        self.spacings.append((display, text))
        previous = self.external_boundary or {}
        self.external_boundary = {
            "display": display,
            "trailing_newlines": len(text) if text.strip("\n") == "" else 0,
            "has_text": bool(previous.get("has_text")) or bool(text.strip())
        }


def build_stream_ui(display_text: str = "") -> tuple[StreamUI, FakeTextState]:
    """构造不启动真实终端 renderer 的流式 UI。"""
    ui = StreamUI.__new__(StreamUI)
    state = FakeTextState(display_text)

    ui._stream_boundary_pending = False
    ui.coordinator = SimpleNamespace(text_state=state)
    ui.settle_stream = AsyncMock()

    async def commit_live() -> None:
        state.display_text = ""

    ui.commit_live = AsyncMock(side_effect=commit_live)
    ui._print_boundary_prefix = AsyncMock()
    return ui, state


def test_prepare_external_output_is_noop_without_content_or_spacing() -> None:
    """没有正文和外部间距时不触碰 renderer。"""
    ui, _ = build_stream_ui()

    asyncio.run(ui.prepare_external_output())

    ui.settle_stream.assert_not_awaited()
    ui.commit_live.assert_not_awaited()
    ui._print_boundary_prefix.assert_not_awaited()


def test_prepare_external_output_commits_live_only_once() -> None:
    """连续准备外部输出时只落版一次有效正文。"""
    ui, state = build_stream_ui("assistant reply")

    asyncio.run(ui.prepare_external_output())
    asyncio.run(ui.prepare_external_output())

    ui.settle_stream.assert_awaited_once_with()
    ui.commit_live.assert_awaited_once_with()
    ui._print_boundary_prefix.assert_awaited_once_with("\n")
    assert state.spacings == [(StreamUI.BLOCK, "\n")]


def test_prepare_external_output_preserves_required_external_spacing() -> None:
    """没有 live 正文时仍补足已有外部输出的段间空行。"""
    ui, state = build_stream_ui()
    state.external_boundary = {
        "display": StreamUI.BLOCK,
        "trailing_newlines": 1,
        "has_text": True
    }

    asyncio.run(ui.prepare_external_output())

    ui.settle_stream.assert_not_awaited()
    ui.commit_live.assert_not_awaited()
    ui._print_boundary_prefix.assert_awaited_once_with("\n")
    assert state.spacings == [(StreamUI.BLOCK, "\n")]


def test_stream_tool_boundary_prepares_external_output(monkeypatch) -> None:
    """工具边界在具体事件状态显示前统一准备外部输出。"""
    calls: list[str] = []

    class FakeStreamUI(object):
        STREAM = "stream"
        BLOCK = "block"

        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def open(self) -> None:
            calls.append("open")

        async def stop(self, *, blink: bool = True) -> None:
            _ = blink
            calls.append("stop")

        async def feed(self, text, **_kwargs) -> None:
            if text:
                calls.append("feed")

        async def settle_stream(self) -> None:
            calls.append("settle")

        def mark_stream_boundary(self) -> None:
            calls.append("mark")

        async def prepare_external_output(self) -> None:
            calls.append("prepare")

        async def begin_reply_wait_status(self, *_args, **_kwargs) -> None:
            calls.append("status")

        async def end_status(self, **_kwargs) -> None:
            calls.append("end")

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
        yield {"type": "text.delta", "text": "reply"}
        yield {"type": "text.done"}
        yield {"type": "tool.calls.start"}
        yield {"type": "turn.done"}

    monkeypatch.setattr(stream_module, "StreamUI", FakeStreamUI)
    monkeypatch.setattr(stream_module.request, "stream_chat", fake_stream_chat)

    asyncio.run(stream_module.stream_looper(
        FakeMind(),
        SimpleNamespace(),
        "fast",
        {},
        "message",
        [],
        exec_env={}
    ))

    assert calls.count("prepare") == 1
    assert calls.index("prepare") < calls.index("status", calls.index("prepare"))
