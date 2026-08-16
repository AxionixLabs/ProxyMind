# -*- coding: utf-8 -*-

import asyncio

import pytest

from mind_nova import events
from mind_nova.events import (
    EventReport,
    EventReportPool,
)
from mind_nova.stream_events import parse_stream_event


def test_report_binds_typed_stream_metadata() -> None:
    report = EventReport("cid", "sid")
    event = parse_stream_event({
        "type": "turn.start",
        "proto": "mind.chat",
        "cid": "cid",
        "sid": "sid",
        "turn_id": "turn_test",
        "event_seq": 1,
        "presentation_epoch": 1,
        "round": 3,
    })

    report.bind_event(event)

    assert report.proto == "mind.chat"
    assert report.round == 3


def test_report_resets_turn_round_and_keeps_default_proto() -> None:
    report = EventReport("cid", "sid")
    report.set_round(3)

    report.begin_turn("next")

    assert report.proto == report.default_proto()
    assert report.round == 1


@pytest.mark.anyio
async def test_worker_preserves_transport_converted_cancellation(monkeypatch) -> None:
    started = asyncio.Event()

    async def post_event(*_args, **_kwargs) -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            raise OSError("transport converted cancellation")

    monkeypatch.setattr(events, "post_stream_event", post_event)
    report = EventReport("cid", "sid")
    report.emit({"type": "probe"})
    await report.open()
    await started.wait()

    worker = report.worker
    assert worker is not None
    worker.cancel()

    with pytest.raises(asyncio.CancelledError):
        await worker

    await report.close(drain=False)
    assert report.worker is None


@pytest.mark.anyio
@pytest.mark.parametrize("drain", (True, False))
async def test_close_exposes_worker_failure(drain: bool) -> None:
    report = EventReport("cid", "sid")
    report.emit({"type": "probe"})

    async def fail() -> None:
        raise RuntimeError("worker failed")

    report.work = fail
    await report.open()
    await asyncio.sleep(0)

    with pytest.raises(RuntimeError, match="worker failed"):
        await asyncio.wait_for(report.close(drain=drain), timeout=1.0)

    assert report.worker is None


@pytest.mark.anyio
async def test_close_drains_events_in_order(monkeypatch) -> None:
    posted: list[int] = []

    async def post_event(_cid, _sid, event, *, timeout) -> None:
        _ = timeout
        posted.append(event["index"])

    monkeypatch.setattr(events, "post_stream_event", post_event)
    report = EventReport("cid", "sid")
    report.emit({"type": "probe", "index": 1})
    report.emit({"type": "probe", "index": 2})
    await report.open()

    await report.close()

    assert posted == [1, 2]
    assert report.worker is None


@pytest.mark.anyio
async def test_interrupted_close_discards_pending_events(monkeypatch) -> None:
    started = asyncio.Event()

    async def post_event(*_args, **_kwargs) -> None:
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(events, "post_stream_event", post_event)
    report = EventReport("cid", "sid")
    report.emit({"type": "probe"})
    await report.open()
    await started.wait()

    await asyncio.wait_for(report.close(drain=False), timeout=1.0)

    assert report.worker is None


@pytest.mark.anyio
async def test_report_pool_reuses_session_worker(
    monkeypatch,
) -> None:
    posted: list[int] = []

    async def post_event(_cid, _sid, event, *, timeout) -> None:
        _ = timeout
        posted.append(event["index"])

    monkeypatch.setattr(events, "post_stream_event", post_event)
    pool = EventReportPool()

    first = await pool.acquire("cid", "sid")
    first.emit({"type": "probe", "index": 1})

    second = await pool.acquire("cid", "sid")
    second.emit({"type": "probe", "index": 2})

    await pool.close_session("cid", "sid")

    assert second is first
    assert posted == [1, 2]
    assert first.worker is None


@pytest.mark.anyio
async def test_closed_report_pool_rejects_new_sessions() -> None:
    pool = EventReportPool()
    await pool.close()

    with pytest.raises(RuntimeError, match="pool is closed"):
        await pool.acquire("cid", "sid")
