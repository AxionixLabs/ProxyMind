# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
from mind_nova.events import EventReport


async def finish_stream(
    ev_report: typing.Optional[EventReport],
    phase: str,
    **extra: typing.Any
) -> None:
    """统一结束流式事件，并确保事件在返回前刷到服务端。"""
    if not ev_report:
        return None
    ev_report.emit({"type": phase, "ts": time.time(), **extra})
    await ev_report.flush()


if __name__ == '__main__':
    pass
