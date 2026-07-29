# -*- coding: utf-8 -*-

import asyncio

import pytest

from mind_nova import events
from mind_nova.events import EventReport


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
    report = EventReport("fast", "cid", "sid")
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
    report = EventReport("fast", "cid", "sid")
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

    async def post_event(_mode, _cid, _sid, event, *, timeout) -> None:
        _ = timeout
        posted.append(event["index"])

    monkeypatch.setattr(events, "post_stream_event", post_event)
    report = EventReport("fast", "cid", "sid")
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
    report = EventReport("fast", "cid", "sid")
    report.emit({"type": "probe"})
    await report.open()
    await started.wait()

    await asyncio.wait_for(report.close(drain=False), timeout=1.0)

    assert report.worker is None
