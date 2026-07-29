# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
import asyncio
import contextlib
from engine.observability import (
    observe,
    observe_exception
)
from mind_nova.identifiers import short_uid
from mind_nova.modes import RunMode
from mind_nova.requests.reports import post_stream_event
from mind_nova.stream_events import StreamEvent
from mind_nova import const


class EventReport(object):
    """事件上报器，保证队列内事件按顺序发送。"""

    @staticmethod
    def default_proto(mode: str) -> str:
        """按运行模式生成事件协议名。"""
        mode_name = str(mode or "").strip().lower()
        return f"{const.APP_NAME}.{mode_name or 'unknown'}"

    def __init__(
        self,
        mode: RunMode,
        cid: str,
        sid: str,
        proto: typing.Optional[str] = None
    ):
        self.mode = mode
        self.cid  = cid
        self.sid  = sid

        self.proto = proto.strip() if isinstance(
            proto, str
        ) and proto.strip() else self.default_proto(mode)

        self.turn_id: str   = short_uid(12)
        self.round: int     = 1
        self.timeout: float = 30.0
        self.seq: int       = 0

        self.q: asyncio.Queue[dict[str, typing.Any]] = asyncio.Queue(maxsize=2000)
        self.stop = asyncio.Event()
        self.worker: typing.Optional[asyncio.Task] = None

    def begin_turn(
        self,
        turn_id: typing.Optional[str] = None,
        *,
        round_no: typing.Optional[int] = None
    ) -> str:
        self.turn_id = str(turn_id or short_uid(12))
        if isinstance(round_no, int) and round_no > 0:
            self.round = round_no
        return self.turn_id

    def set_round(self, round_no: typing.Any) -> None:
        if isinstance(round_no, int) and round_no > 0:
            self.round = round_no

    def bind_event(self, event: StreamEvent) -> None:
        """绑定服务端事件携带的报告元数据。"""
        if event.proto:
            self.proto = event.proto
        self.set_round(event.round)

    def emit(self, event: dict[str, typing.Any]) -> None:
        """
        非阻塞投递事件。
        - 自动注入 cid/sid/ts/seq
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

    async def open(self) -> None:
        """启动后台发送 worker（建议在 pack_start 前调用）"""
        if self.worker and not self.worker.done():
            return None
        self.worker = asyncio.create_task(self.work(), name="event report worker")
        self.worker.add_done_callback(self._worker_done)

    @staticmethod
    def _worker_done(worker: asyncio.Task[None]) -> None:
        """回收后台任务异常，避免事件循环输出未取回异常。"""
        if worker.cancelled():
            return None

        error = worker.exception()
        if error is None or isinstance(error, (KeyboardInterrupt, SystemExit)):
            return None

        observe_exception("event_report.worker_failed", error, level="WARNING")

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
                await post_stream_event(self.mode, self.cid, self.sid, ev, timeout=self.timeout)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                task = asyncio.current_task()
                if task is not None and task.cancelling():
                    raise asyncio.CancelledError from e
                observe_exception(
                    "event_report.post_failed",
                    e,
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

    @staticmethod
    async def _cancel_worker(worker: asyncio.Task[None]) -> None:
        """取消 worker，并仅吸收预期的任务取消异常。"""
        if not worker.done():
            worker.cancel()

        try:
            await worker
        except asyncio.CancelledError:
            return None

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


if __name__ == '__main__':
    pass
