# -*- coding: utf-8 -*-

import asyncio
import typing
from dataclasses import dataclass

from mind_app.modes import batch


@dataclass(slots=True)
class DummySource:
    """测试用批处理源。"""
    content: str
    display_origin: str


class DummyEventReport(object):
    """记录批处理事件报告调用。"""

    instances: list["DummyEventReport"] = []

    def __init__(self, mode: str, cid: str, sid: str, proto: str) -> None:
        self.mode = mode
        self.cid = cid
        self.sid = sid
        self.proto = proto
        self.events: list[dict[str, typing.Any]] = []
        self.open_count = 0
        self.flush_count = 0
        self.close_count = 0
        self.turns: list[int] = []
        self.instances.append(self)

    async def open(self) -> None:
        """记录打开事件报告。"""
        self.open_count += 1

    def begin_turn(self, *, round_no: int) -> None:
        """记录轮次开始。"""
        self.turns.append(round_no)

    def emit(self, event: dict[str, typing.Any]) -> None:
        """记录诊断事件。"""
        self.events.append(event)

    async def flush(self) -> None:
        """记录写出事件报告。"""
        self.flush_count += 1

    async def close(self) -> None:
        """记录关闭事件报告。"""
        self.close_count += 1


class DummyMind(object):
    """提供批处理测试所需的最小 Mind 接口。"""

    def __init__(self) -> None:
        self.sessions: list[dict[str, typing.Any]] = []
        self.session_calls: list[dict[str, typing.Any]] = []
        self.cleanup_count = 0

    async def fresh_pref_config(self, *, ttl_sec: float | None = None) -> dict[str, typing.Any]:
        """返回测试偏好配置。"""
        _ = ttl_sec
        return {"primary": {"model": "test-model"}}

    def begin_session(
        self,
        cid: str | None = None,
        sid: str | None = None,
        **kwargs: typing.Any
    ) -> dict[str, str]:
        """记录会话初始化参数。"""
        self.sessions.append({"cid": cid, "sid": sid, **kwargs})
        return {"cid": cid or "cid_batch", "sid": sid or "sid_batch"}

    async def with_mcp_session(
        self,
        pref_config: dict[str, typing.Any],
        function: typing.Callable[..., typing.Awaitable[None]],
        before_user_flow: typing.Callable[[], typing.Any] | None = None
    ) -> None:
        """执行传入的 MCP 会话回调。"""
        self.session_calls.append({"pref_config": pref_config})
        if before_user_flow is not None:
            before_user_flow()
        await function(object(), [{"name": "tool"}])

    async def await_cleanup(self, awaitable: typing.Awaitable[None]) -> None:
        """直接等待清理协程。"""
        self.cleanup_count += 1
        await awaitable


def test_mind_pack_prepares_context_and_closes_report(monkeypatch) -> None:
    """批处理入口准备上下文、执行源并关闭事件报告。"""
    mind = DummyMind()
    source = DummySource(content="hello", display_origin="case.md")
    source_calls: list[dict[str, typing.Any]] = []

    async def fake_resolve_code_sources(code: list[typing.Any]) -> list[DummySource]:
        """返回固定批处理源。"""
        assert code == ["case.md"]
        return [source]

    def fake_resolve_mode_runner(actual_mind: DummyMind, mode: str) -> typing.Any:
        """返回固定运行器。"""
        assert actual_mind is mind
        assert mode == "chat"
        return object()

    async def fake_open_report_session(mode: str, cid: str, sid: str, proto: str) -> dict[str, str]:
        """返回固定报告地址。"""
        assert (mode, cid, sid, proto) == ("chat", "cid_batch", "sid_batch", "mind.batch")
        return {"report_url": "https://report.example/r/1", "report_id": "report_1"}

    async def fake_run_pack_source(
        actual_mind: DummyMind,
        runtime: batch.PackRuntime,
        actual_source: DummySource,
        session: object,
        tools: list[dict[str, typing.Any]],
        **kwargs: typing.Any
    ) -> None:
        """记录批处理源执行参数。"""
        source_calls.append({
            "mind": actual_mind,
            "runtime": runtime,
            "source": actual_source,
            "session": session,
            "tools": tools,
            "kwargs": kwargs,
        })

    DummyEventReport.instances = []
    monkeypatch.setattr(batch, "resolve_code_sources", fake_resolve_code_sources)
    monkeypatch.setattr(batch, "resolve_mode_runner", fake_resolve_mode_runner)
    monkeypatch.setattr(batch, "open_report_session", fake_open_report_session)
    monkeypatch.setattr(batch, "EventReport", DummyEventReport)
    monkeypatch.setattr(batch, "_run_pack_source", fake_run_pack_source)

    asyncio.run(
        batch.mind_pack(
            mind,
            ["case.md"],
            "chat",
            metadata={"caller_tag": "remote"}
        )
    )

    assert mind.sessions == [{
        "cid": None,
        "sid": None,
        "title": "hello",
        "source": "batch",
    }]
    assert mind.session_calls == [{"pref_config": {"primary": {"model": "test-model"}}}]
    assert len(DummyEventReport.instances) == 1
    event_report = DummyEventReport.instances[0]
    assert event_report.open_count == 1
    assert event_report.turns == [1]
    assert event_report.flush_count == 1
    assert event_report.close_count == 1
    assert mind.cleanup_count == 2
    assert len(source_calls) == 1
    assert source_calls[0]["mind"] is mind
    assert source_calls[0]["source"] is source
    assert source_calls[0]["tools"] == [{"name": "tool"}]
    assert source_calls[0]["runtime"].event_report is event_report
    assert source_calls[0]["kwargs"]["metadata"] == {
        "caller_tag": "remote",
        "cid": "cid_batch",
        "sid": "sid_batch",
    }
    assert source_calls[0]["kwargs"]["ev_report"] is event_report
