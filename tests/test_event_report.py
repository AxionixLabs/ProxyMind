# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace

import mind_app.modes.stream as stream_module
from mind_app.output.legacy_content import LegacyContentSink
from mind_app.output.session import OutputSession
from mind_app.presentation.legacy import LegacyPresentationSink
from mind_nova.events import EventReport


def test_bind_event_keeps_client_turn_id() -> None:
    """服务端流事件不能覆盖客户端生成的轮次标识。"""
    report = EventReport("chat", "cid-test", "sid-test")
    turn_id = report.turn_id

    report.bind_event({
        "turn_id": "server-turn-id",
        "proto": "mind.chat",
        "round": 2
    })

    assert report.turn_id == turn_id
    assert report.proto == "mind.chat"
    assert report.round == 2


def test_stream_request_uses_event_report_turn_id(monkeypatch) -> None:
    """流式请求和事件上报使用同一个客户端轮次标识。"""
    request_kwargs = {}

    class FakeStreamUI(object):
        BLOCK = "block"

        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def open(self) -> None:
            return None

        async def stop(self, *, blink: bool = True) -> None:
            _ = blink

        async def end_status(self, **_kwargs) -> None:
            return None

        async def feed(self, _text: str, **_kwargs) -> None:
            return None

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

    async def fake_stream_chat(*_args, **kwargs):
        request_kwargs.update(kwargs)
        yield {"type": "turn.start", "turn_id": "server-turn-id"}
        yield {"type": "turn.done", "turn_id": "server-turn-id"}

    report = EventReport("chat", "cid-test", "sid-test")
    initial_turn_id = report.turn_id

    monkeypatch.setattr(stream_module.request, "stream_chat", fake_stream_chat)

    def session_factory(*_args, **_kwargs) -> OutputSession:
        output = FakeStreamUI()
        return OutputSession(
            control=output,
            content=LegacyContentSink(output),
            presentation=LegacyPresentationSink(output),
        )

    asyncio.run(stream_module.stream_looper(
        FakeMind(),
        SimpleNamespace(),
        "chat",
        {},
        "hello",
        [],
        exec_env={},
        ev_report=report,
        session_factory=session_factory,
    ))

    assert report.turn_id != initial_turn_id
    assert request_kwargs["turn_id"] == report.turn_id
