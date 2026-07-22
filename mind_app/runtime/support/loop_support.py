# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_nova.events import EventReport
from ...output import OutputStatusPort
from ...presentation.contracts import PresentationSink
from ...presentation.lifecycle_views import build_failure_view
from ...stream_events.finish import finish_stream


async def finish_failure(
    status_control: OutputStatusPort,
    presentation: PresentationSink,
    ev_report: typing.Optional[EventReport],
    *,
    phase: str,
    error: typing.Any,
    **extra: typing.Any
) -> None:
    """统一结束失败事件并输出用户可见错误。"""
    message = "" if error is None else str(error)

    await finish_stream(ev_report, phase=phase, error=message, **extra)
    await status_control.end_status(immediate=True)

    await presentation.emit(build_failure_view(phase, message))


if __name__ == '__main__':
    pass
