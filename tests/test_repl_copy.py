# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace

import mind_app.modes.support.repl_commands as repl_commands
from mind_app.stream_events.assistant_boundary import is_assistant_output_boundary
from mind_app.stream_state.segment import SegmentTracker


def run_async(value: object) -> object:
    """同步运行异步测试目标。"""
    return asyncio.run(value)


def test_segment_tracker_assistant_text_keeps_markdown_raw() -> None:
    """模型正文缓存保留 Markdown 原文。"""
    tracker = SegmentTracker()

    tracker.on_text_delta({"type": "text.delta", "text": "# Title\n"})
    tracker.on_text_delta({"type": "text.delta", "text": "\n```python\n"})
    tracker.on_text_delta({"type": "text.delta", "text": "print('hi')\n```"})
    tracker.on_text_meta({"type": "text.meta", "segment_id": "seg-1", "sources": ["https://example.test"]})

    assert tracker.assistant_text() == "# Title\n\n```python\nprint('hi')\n```"


def test_segment_tracker_assistant_text_separates_segments() -> None:
    """多个正文段落会按流式边界拼接。"""
    tracker = SegmentTracker()

    tracker.on_text_delta({"type": "text.delta", "text": "first"})
    tracker.on_text_done({"type": "text.done"})
    tracker.on_text_delta({"type": "text.delta", "text": "second"})

    assert tracker.assistant_text() == "first\nsecond"


def test_segment_tracker_latest_output_keeps_segments_without_boundary() -> None:
    """没有外部输出边界时复制块保留多个文本段。"""
    tracker = SegmentTracker()

    tracker.on_text_delta({"type": "text.delta", "text": "first"})
    tracker.on_text_done({"type": "text.done"})
    tracker.on_text_delta({"type": "text.delta", "text": "second"})

    assert tracker.latest_assistant_output_text() == "first\nsecond"


def test_segment_tracker_latest_output_uses_committed_boundary() -> None:
    """外部输出边界后只复制最近的 assistant 输出块。"""
    tracker = SegmentTracker()

    tracker.on_text_delta({"type": "text.delta", "text": "checking first"})
    tracker.on_text_done({"type": "text.done"})
    tracker.commit_assistant_output()
    tracker.on_text_delta({"type": "text.delta", "text": "final answer"})

    assert tracker.assistant_text() == "checking first\nfinal answer"
    assert tracker.latest_assistant_output_text() == "final answer"


def test_segment_tracker_latest_output_keeps_previous_when_boundary_has_no_text() -> None:
    """边界后没有新正文时复制最近的 assistant 输出块。"""
    tracker = SegmentTracker()

    tracker.on_text_delta({"type": "text.delta", "text": "only answer"})
    tracker.commit_assistant_output()
    tracker.commit_assistant_output()

    assert tracker.latest_assistant_output_text() == "only answer"


def test_stream_tool_event_is_assistant_output_boundary() -> None:
    """工具事件会结束当前 assistant 输出块。"""
    assert is_assistant_output_boundary("tool.call", {"type": "tool.call"})


def test_stream_display_event_is_assistant_output_boundary() -> None:
    """显式展示事件会结束当前 assistant 输出块。"""
    event = {"type": "custom.event", "display": {"message": "external output"}}

    assert is_assistant_output_boundary("custom.event", event)


def test_copy_last_assistant_reply_uses_clipboard_helper(monkeypatch) -> None:
    """复制命令使用剪贴板 helper 写入缓存原文。"""
    copied: list[str] = []

    async def fake_copy(text: str) -> None:
        copied.append(text)

    monkeypatch.setattr(repl_commands, "copy_text_to_clipboard", fake_copy)

    mind = SimpleNamespace(last_assistant_reply_snapshot=lambda: "**raw**")

    run_async(repl_commands.copy_last_assistant_reply(mind))

    assert copied == ["**raw**"]


def test_copy_last_assistant_reply_ignores_empty_message(monkeypatch) -> None:
    """空回复不会调用剪贴板 helper。"""
    copied: list[str] = []

    async def fake_copy(text: str) -> None:
        copied.append(text)

    monkeypatch.setattr(repl_commands, "copy_text_to_clipboard", fake_copy)

    mind = SimpleNamespace(last_assistant_reply_snapshot=lambda: "")

    run_async(repl_commands.copy_last_assistant_reply(mind))

    assert copied == []
