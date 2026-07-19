# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace

import mind_app.modes.stream as stream_module
from mind_app.output import (
    AssistantTextDelta,
    ContentOutput,
    SourcesOutput,
)
from mind_app.output.legacy_content import LegacyContentSink
from mind_app.output.session import OutputSession
from mind_app.presentation.legacy import LegacyPresentationSink


class RecordingContentSink(object):
    """记录流式运行时发送的结构化正文内容。"""

    def __init__(self) -> None:
        self.outputs: list[ContentOutput] = []

    async def emit(self, output: ContentOutput) -> None:
        self.outputs.append(output)


class FakeOutput(object):
    """记录内容适配和流式生命周期调用。"""

    def __init__(self) -> None:
        self.feeds: list[tuple[str, dict]] = []
        self.end_count = 0
        self.stop_count = 0

    async def open(self) -> None:
        return None

    async def stop(self, *, blink: bool = True) -> None:
        _ = blink
        self.stop_count += 1

    async def feed(self, text: str, **kwargs) -> None:
        self.feeds.append((text, kwargs))

    async def end_status(self, *, immediate: bool = False) -> None:
        _ = immediate
        self.end_count += 1

    async def begin_reply_wait_status(self, *_args, **_kwargs) -> None:
        return None


def test_legacy_content_sink_preserves_text_and_sources_output() -> None:
    """默认内容适配器保持原有 stream 和 block 输出。"""
    output = FakeOutput()
    sink = LegacyContentSink(output)
    sources = (
        {"title": "First", "url": "https://one.test"},
        "https://two.test",
        {"name": "Third"},
        {"title": "Duplicate", "url": "https://one.test"},
        "https://four.test",
    )

    async def run() -> None:
        await sink.emit(AssistantTextDelta("hello"))
        await sink.emit(SourcesOutput(sources))

    asyncio.run(run())

    assert output.feeds == [
        ("hello", {"display": "stream"}),
        (
            "Sources:\n"
            "1. First\n"
            "   https://one.test\n"
            "2. https://two.test\n"
            "3. Third\n"
            "... 1 more sources omitted",
            {"display": "block"},
        ),
    ]


def test_stream_looper_sends_content_to_injected_sink(monkeypatch) -> None:
    """流式运行时把正文和原始来源发送给注入的内容端。"""
    output = FakeOutput()
    content = RecordingContentSink()
    request_kwargs: dict = {}

    class FakeMind(object):
        report = SimpleNamespace(log_papers="unused")
        level = "show"

        def __init__(self) -> None:
            self.last_reply = ""
            self.frontend = SimpleNamespace(
                session_factory=lambda *_args, **_kwargs: OutputSession(
                    control=output,
                    content=content,
                    presentation=LegacyPresentationSink(output),
                )
            )

        def is_service_mcp_linked(self) -> bool:
            return False

        async def stop_anim(self) -> None:
            return None

        def remember_last_assistant_reply(self, text: str) -> None:
            self.last_reply = text

        async def await_cleanup(self, awaitable) -> None:
            await awaitable

    async def fake_stream_chat(*_args, **kwargs):
        request_kwargs.update(kwargs)
        yield {"type": "turn.start"}
        yield {"type": "text.delta", "segment_id": "seg-1", "text": "answer"}
        yield {
            "type": "text.meta",
            "segment_id": "seg-1",
            "sources": [{"title": "Source", "url": "https://source.test"}],
        }
        yield {"type": "turn.done"}

    monkeypatch.setattr(stream_module.request, "stream_chat", fake_stream_chat)
    mind = FakeMind()

    asyncio.run(stream_module.stream_looper(
        mind,
        SimpleNamespace(),
        "fast",
        {},
        "message",
        [],
        exec_env={},
    ))

    assert len(content.outputs) == 2
    assert content.outputs[0] == AssistantTextDelta("answer")
    assert content.outputs[1] == SourcesOutput((
        {"title": "Source", "url": "https://source.test"},
    ))
    assert output.feeds == []
    assert output.end_count == 1
    assert output.stop_count == 1
    assert mind.last_reply == "answer"
    assert "session_factory" not in request_kwargs


if __name__ == '__main__':
    pass
