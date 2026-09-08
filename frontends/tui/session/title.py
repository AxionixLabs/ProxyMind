# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from agent.ports.presentation import (
    ApplicationView,
    TextSpan,
)
from metadata import const
from protocol.schema.stream_events import SessionTitleUpdatedEvent
from ..core.styles import (
    BODY_STYLE,
    TERMINAL_CYAN_STYLE,
    fragment_block,
)

if typing.TYPE_CHECKING:
    from ..application import TuiApplicationHost


def project_session_title_update(
    host: "TuiApplicationHost",
    event: SessionTitleUpdatedEvent,
) -> bool:
    """持久化并展示属于当前根会话的服务端标题更新。"""
    if not host.conversation.update_title(
        event.cid,
        event.sid,
        event.title,
        source="stream",
    ):
        return False

    command = f"{const.APP_NAME} resume"
    host.frontend.application.emit(ApplicationView(
        type="session.title.updated",
        renderable=fragment_block(
            TextSpan("• Session renamed to ", BODY_STYLE),
            TextSpan(event.title, TERMINAL_CYAN_STYLE),
            TextSpan(
                ". To resume this session run ",
                BODY_STYLE,
            ),
            TextSpan(command, TERMINAL_CYAN_STYLE),
            TextSpan(", then select ", BODY_STYLE),
            TextSpan(
                f"{event.title} ({event.sid})",
                TERMINAL_CYAN_STYLE,
            ),
        ),
    ))
    return True


if __name__ == '__main__':
    pass
