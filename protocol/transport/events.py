# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import contextlib
import time
import typing

from observability import (
    observe_exception,
    observe,
)
from protocol.schema.identifiers import short_uid
from protocol.schema.stream_events import StreamEvent
from protocol.transport.reports import post_stream_event


class EventReport(object):
    """事件上报器，保证队列内事件按顺序发送。"""

    def __init__(
        self,
        cid: str,
        sid: str,
    ):
        self.cid = cid
        self.sid = sid
        self.proto = self.default_proto()
        self.turn_id: str = short_uid(12)
        self.presentation_epoch: int = 1
        self.round: int = 1
        self.timeout: float = 30.0
        self.seq: int = 0
        self.q: asyncio.Queue[dict[str, typing.Any]] = asyncio.Queue(maxsize=2000)
        self.stop: asyncio.Event = asyncio.Event()
        self.worker: typing.Optional[asyncio.Task] = None

    @staticmethod
    def _worker_done(worker: asyncio.Task[None]) -> None:
        """回收后台任务异常，避免事件循环输出未取回异常。"""
        if worker.cancelled():
            return None

        error = worker.exception()
        if error is None or isinstance(error, (KeyboardInterrupt, SystemExit)):
            return None

        observe_exception(
            "event_report.worker_failed",
            error,
            level="WARNING",
        )

    @staticmethod
    def default_proto() -> str:
        """返回默认事件协议名。"""
        return "mind.chat"

    def begin_turn(
        self,
        turn_id: typing.Optional[str] = None,
        *,
        round_no: typing.Optional[int] = None
    ) -> str:
        self.turn_id = str(turn_id or short_uid(12))

        self.round = (
            round_no
            if isinstance(round_no, int) and round_no > 0
            else 1
        )
        self.presentation_epoch = 1

        return self.turn_id

    def set_round(self, round_no: typing.Any) -> None:
        if isinstance(round_no, int) and round_no > 0:
            self.round = round_no

    def bind_event(self, event: StreamEvent) -> None:
        """绑定服务端事件携带的报告元数据。"""
        if event.proto:
            self.proto = event.proto
        self.presentation_epoch = event.presentation_epoch
        self.set_round(event.round)

    def emit(self, event: dict[str, typing.Any]) -> None:
        """
        非阻塞投递事件。
        - 自动注入协议坐标、展示轮次和序号
        - 队列满则丢弃（避免拖死主链路）
        """
        try:
            self.seq += 1
            ev = dict(event or {})
            ev.setdefault("ts", time.time())
            ev.setdefault("proto", self.proto)
            ev["cid"] = self.cid
            ev["sid"] = self.sid
            ev.setdefault("turn_id", self.turn_id)
            ev["presentation_epoch"] = self.presentation_epoch
            ev.setdefault("round", self.round)
            ev.setdefault("seq", self.seq)

            self.q.put_nowait(ev)

        except asyncio.QueueFull:
            observe(
                "event_report.dropped",
                level="WARNING",
                reason="queue_full",
                event_type=event.get("type"),
            )

        except RuntimeError:
            observe(
                "event_report.dropped",
                level="WARNING",
                reason="no_loop",
                event_type=event.get("type"),
            )

    @staticmethod
    async def _cancel_worker(worker: asyncio.Task[None]) -> None:
        """取消 worker，并仅吸收预期的任务取消异常。"""
        if not worker.done():
            worker.cancel()

        try:
            await worker
        except asyncio.CancelledError:
            return None

    async def open(self) -> None:
        """启动后台发送 worker（建议在 pack_start 前调用）"""
        if self.worker and not self.worker.done():
            return None
        self.worker = asyncio.create_task(self.work(), name="event report worker")
        self.worker.add_done_callback(self._worker_done)

    async def work(self) -> None:
        """单 worker：严格按队列顺序发送"""
        while True:
            if self.stop.is_set() and self.q.empty():
                return None

            try:
                ev = await asyncio.wait_for(self.q.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue

            try:
                await post_stream_event(
                    self.cid,
                    self.sid,
                    ev,
                    timeout=self.timeout,
                )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                task = asyncio.current_task()
                if task is not None and task.cancelling():
                    raise asyncio.CancelledError from error
                observe_exception(
                    "event_report.post_failed",
                    error,
                    level="WARNING",
                    event_type=ev.get("type"),
                    seq=ev.get("seq"),
                )
            finally:
                self.q.task_done()

    async def flush(self) -> None:
        """等待队列清空，并在 worker 提前结束时立即失败。"""
        worker = self.worker
        if worker is None:
            await self.q.join()
            return None

        joined = asyncio.create_task(self.q.join())
        try:
            done, _ = await asyncio.wait(
                (joined, worker),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if worker in done:
                await worker
            await joined
        finally:
            if not joined.done():
                joined.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await joined

    async def close(self, *, drain: bool = True) -> None:
        """停止 worker，并按需发送队列中的剩余事件。"""
        worker = self.worker
        if worker is None:
            return None

        self.stop.set()
        try:
            if not drain:
                await self._cancel_worker(worker)
                return None

            try:
                await self.flush()
                await worker
            finally:
                if not worker.done():
                    await self._cancel_worker(worker)
        finally:
            self.worker = None


class EventReportPool(object):
    """复用同一会话的事件上报通道，并统一管理关闭时机。"""

    def __init__(self) -> None:
        self._reports: dict[tuple[str, str], EventReport] = {}
        self._lock: asyncio.Lock = asyncio.Lock()
        self._closed: bool = False

    async def acquire(
        self,
        cid: str,
        sid: str,
    ) -> EventReport:
        """返回已经启动的会话上报器。"""
        key = (cid, sid)

        async with self._lock:
            if self._closed:
                raise RuntimeError("event report pool is closed")

            report = self._reports.get(key)
            if report is None:
                report = EventReport(cid, sid)
                self._reports[key] = report

            await report.open()

            return report

    async def close_session(
        self,
        cid: str,
        sid: str,
        *,
        drain: bool = True,
    ) -> None:
        """关闭并移除指定会话的上报器。"""
        async with self._lock:
            report = self._reports.pop((cid, sid), None)
        if report is not None:
            await report.close(drain=drain)

    async def close(self, *, drain: bool = True) -> None:
        """关闭池中全部上报器。"""
        async with self._lock:
            if self._closed and not self._reports:
                return None
            self._closed = True
            reports_by_session = self._reports
            self._reports = {}

        if reports_by_session:
            await asyncio.gather(*(
                report.close(drain=drain)
                for report in reports_by_session.values()
            ))


if __name__ == '__main__':
    pass
