# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import enum
import typing

from protocol.transport.events import (
    EventReport,
    EventReportPool,
)


EventReportFactory = typing.Callable[[str, str], EventReport]


class EventReportLifetime(enum.Enum):
    """区分单轮独占报告与根会话复用报告的生命周期。"""

    TURN = "turn"
    SESSION = "session"


class TurnEventReportHandle(object):
    """持有单次轮次使用的事件报告及其释放策略。"""

    def __init__(
        self,
        owner: "EventReportRuntimeOwner",
        report: EventReport,
        *,
        cid: str,
        sid: str,
        lifetime: EventReportLifetime,
    ) -> None:
        """绑定报告、会话标识和释放策略。"""
        self.report = report
        self._owner = owner
        self._cid = cid
        self._sid = sid
        self._lifetime = lifetime

    async def release(self, *, interrupted: bool) -> None:
        """按生命周期释放报告，中断时丢弃未交付事件。"""
        if self._lifetime is EventReportLifetime.TURN:
            await self.report.close(drain=not interrupted)
        elif interrupted:
            await self._owner.close_session(
                self._cid,
                self._sid,
                drain=False,
            )


class EventReportRuntimeOwner(object):
    """持有会话事件报告池，并统一管理单轮和会话报告。"""

    def __init__(
        self,
        *,
        pool: EventReportPool | None = None,
        report_factory: EventReportFactory | None = None,
    ) -> None:
        """绑定可选报告池和单轮报告工厂。"""
        self._pool = pool if pool is not None else EventReportPool()
        self._report_factory = report_factory or EventReport

    async def acquire(
        self,
        cid: str,
        sid: str,
        *,
        lifetime: EventReportLifetime,
    ) -> TurnEventReportHandle:
        """获取已启动的事件报告及对应释放句柄。"""
        if lifetime is EventReportLifetime.SESSION:
            report = await self._pool.acquire(cid, sid)
        elif lifetime is EventReportLifetime.TURN:
            report = self._report_factory(cid, sid)
            await report.open()
        else:
            raise ValueError(f"unsupported event report lifetime: {lifetime!r}")

        return TurnEventReportHandle(
            self,
            report,
            cid=cid,
            sid=sid,
            lifetime=lifetime,
        )

    async def close_session(
        self,
        cid: str,
        sid: str,
        *,
        drain: bool = True,
    ) -> None:
        """关闭指定会话的复用报告。"""
        await self._pool.close_session(cid, sid, drain=drain)

    async def close(self) -> None:
        """关闭全部会话报告并终止后续获取。"""
        await self._pool.close()


if __name__ == '__main__':
    pass
