# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_nova.events import EventReport
from ...stream_ui import StreamUI
from ...stream_events.failure_display import (
    render_failure_display_parts,
    render_failure_text
)
from ...stream_events.finish import finish_stream


async def finish_failure(
    stream_ui: StreamUI,
    ev_report: typing.Optional[EventReport],
    *,
    phase: str,
    error: typing.Any,
    **extra: typing.Any
) -> None:
    """统一结束失败事件并输出用户可见错误。"""
    message = "" if error is None else str(error)
    await finish_stream(ev_report, phase=phase, error=message, **extra)
    await stream_ui.end_status(immediate=True)
    await stream_ui.feed(
        render_failure_text(phase, message),
        display=StreamUI.BLOCK,
        display_parts=render_failure_display_parts(phase, message),
        preserve_display_parts=True
    )


if __name__ == '__main__':
    pass
