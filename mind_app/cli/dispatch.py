# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from .selection import resolve_code_mode

if typing.TYPE_CHECKING:
    from ..controller import Mind


async def run_selected_mode(
    mind: "Mind",
    cmd_lines: typing.Any,
    cli_attachments: list[dict[str, typing.Any]] | None,
) -> None:
    """按命令行参数分派到直接执行或交互模式。"""
    access_mode = "full" if cmd_lines.access else "safe"
    if cmd_lines.agent:
        await mind.agent_loop()
    elif chat := cmd_lines.chat:
        await mind.calling(
            message=chat,
            mode="chat",
            attachments=cli_attachments,
            access_mode=access_mode,
        )
    elif fast := cmd_lines.fast:
        await mind.calling(
            message=fast,
            mode="fast",
            attachments=cli_attachments,
            access_mode=access_mode,
        )
    elif xtra := cmd_lines.xtra:
        await mind.calling(
            message=xtra,
            mode="xtra",
            attachments=cli_attachments,
            access_mode=access_mode,
        )
    elif code := cmd_lines.code:
        await mind.mind_pack(
            code,
            resolve_code_mode(cmd_lines),
            access_mode=access_mode,
        )
    else:
        from ..tui.session.loop import run_tui_loop

        await run_tui_loop(mind)


if __name__ == '__main__':
    pass
