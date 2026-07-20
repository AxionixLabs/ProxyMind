# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from engine.errors import MindError
from mind_app.presentation.renderers.upload import (
    upload_failure_block,
    upload_summary_block
)
from ..frontend import ApplicationView

if typing.TYPE_CHECKING:
    from ..controller import Mind


async def resolve_cli_attachments(
    mind: "Mind",
    cmd_lines: typing.Any,
) -> list[dict[str, typing.Any]] | None:
    """解析并上传直接执行命令携带的附件。"""
    raw_attachments = cmd_lines.attach or []
    if not raw_attachments:
        return None
    if cmd_lines.code:
        raise MindError("--attach is not supported together with --code yet")
    if cmd_lines.chat is None and cmd_lines.fast is None and cmd_lines.xtra is None:
        raise MindError("--attach requires --chat, --fast, or --xtra")

    for raw_path in raw_attachments:
        mind.attach.add_pending_attachments(raw_path)

    pending = mind.attach.pending_attachments_snapshot()

    upload_state: dict[str, typing.Any] = {
        "event": None,
        "item_total": len(pending),
        "total_bytes": sum(int(item.get("size") or 0) for item in pending),
    }

    async def capture_progress(event: dict[str, typing.Any]) -> None:
        upload_state["event"] = dict(event)

    try:
        await mind.start_upload_anim(lambda: dict(upload_state))
        uploaded = await mind.attach.upload_pending_attachments(
            progress_callback=capture_progress
        )
    except MindError as error:
        failure_reason = str(getattr(error, "display_reason", "") or error)
        mind.frontend.application.emit(ApplicationView(
            type="attachment.failure",
            renderable=upload_failure_block(
                message=failure_reason,
                event=upload_state["event"],
            ),
        ))
        raise
    finally:
        await mind.await_cleanup(mind.stop_anim())

    mind.attach.clear_pending_attachments()
    if upload_state["event"] is not None:
        mind.frontend.application.emit(ApplicationView(
            type="attachment.completed",
            renderable=upload_summary_block(upload_state["event"]),
        ))
    return uploaded


if __name__ == '__main__':
    pass
