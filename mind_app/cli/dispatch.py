# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
import asyncio
from ..observability import (
    observe,
    observe_exception
)
from .selection import resolve_code_mode

if typing.TYPE_CHECKING:
    from ..controller import Mind


async def run_selected_mode(
    mind: "Mind",
    cmd_lines: typing.Any,
) -> None:
    """按命令行参数分派到直接执行或交互模式。"""
    access_mode = "full" if cmd_lines.access else "safe"

    if cmd_lines.agent:
        selected_mode = "agent"
    elif cmd_lines.chat:
        selected_mode = "chat"
    elif cmd_lines.fast:
        selected_mode = "fast"
    elif cmd_lines.xtra:
        selected_mode = "xtra"
    elif cmd_lines.code:
        selected_mode = resolve_code_mode(cmd_lines)
    else:
        selected_mode = "tui"

    started_at = time.perf_counter()
    observe("mode.start", mode=selected_mode, access_mode=access_mode)

    try:
        if cmd_lines.agent:
            await mind.agent_loop()
        elif chat := cmd_lines.chat:
            await mind.calling(
                message=chat,
                mode="chat",
                access_mode=access_mode,
            )
        elif fast := cmd_lines.fast:
            await mind.calling(
                message=fast,
                mode="fast",
                access_mode=access_mode,
            )
        elif xtra := cmd_lines.xtra:
            await mind.calling(
                message=xtra,
                mode="xtra",
                access_mode=access_mode,
            )
        elif code := cmd_lines.code:
            await mind.mind_pack(
                code,
                selected_mode,
                access_mode=access_mode,
            )
        else:
            from ..tui.session.loop import run_tui_loop

            await run_tui_loop(mind)
    except asyncio.CancelledError:
        observe(
            "mode.interrupted",
            level="WARNING",
            mode=selected_mode,
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
        )
        raise
    except BaseException as error:
        observe_exception(
            "mode.failed",
            error,
            mode=selected_mode,
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
        )
        raise
    else:
        observe(
            "mode.complete",
            mode=selected_mode,
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
        )


if __name__ == '__main__':
    pass
