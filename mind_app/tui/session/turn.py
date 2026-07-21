# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from engine.errors import MindError
from mind_app.mcp.contracts import McpSessionLike
from mind_app.frontend import ApplicationView
from mind_app.presentation.renderers.upload import (
    upload_failure_block,
    upload_summary_block
)
from mind_nova.events import EventReport
from mind_nova.modes import RunMode
from ...runtime.support.calling import resolve_mode_runner
from ..core.models import FragmentBlock
from ..core.styles import styled_block_fragments

if typing.TYPE_CHECKING:
    from ...controller import Mind


async def run_tui_model_turn(
    mind: "Mind",
    *,
    message_text: str,
    run_mode: RunMode,
    pref_config: dict[str, typing.Any],
    access_mode: str = "safe"
) -> None:
    """为单轮 TUI 输入建立 MCP 会话并执行模型流程。"""
    async def run_turn_with_session(
        session: McpSessionLike,
        tools: list[dict[str, typing.Any]],
    ) -> None:
        runner = resolve_mode_runner(mind, run_mode)

        uploaded_attachments: typing.Optional[list[dict[str, typing.Any]]] = None

        if mind.attach.has_pending_attachments():
            uploaded_attachments = await upload_pending_tui_attachments(mind)
            if uploaded_attachments is None:
                return None

        turn_metadata = mind.begin_session(title=message_text, source="tui")
        ev_report     = EventReport(run_mode, turn_metadata["cid"], turn_metadata["sid"])

        await ev_report.open()

        try:
            await mind.run_mode_lifecycle(
                runner,
                mode=run_mode,
                session=session,
                pref_config=pref_config,
                message=message_text,
                tools=tools,
                attachments=uploaded_attachments,
                access_mode=access_mode,
                metadata=turn_metadata,
                ev_report=ev_report
            )

        finally:
            await ev_report.flush()
            await ev_report.close()

            if uploaded_attachments:
                mind.attach.clear_pending_attachments()

    await mind.with_mcp_session(pref_config, run_turn_with_session)


async def upload_pending_tui_attachments(
    mind: "Mind"
) -> typing.Optional[list[dict[str, typing.Any]]]:
    """上传当前待发送附件；失败时打印错误并返回 None。"""
    attachments = mind.attach.pending_attachments_snapshot()

    upload_state: dict[str, typing.Any] = {
        "event"       : None,
        "item_total"  : len(attachments),
        "total_bytes" : sum(int(attachment.get("size") or 0) for attachment in attachments)
    }

    async def capture_progress(event: dict[str, typing.Any]) -> None:
        upload_state["event"] = dict(event)

    try:
        await mind.start_upload_anim(lambda: dict(upload_state))
        uploaded_attachments = await mind.attach.upload_pending_attachments(
            progress_callback=capture_progress
        )
    except MindError as upload_error:
        failure_reason = str(getattr(upload_error, "display_reason", "") or upload_error)
        mind.frontend.application.emit(ApplicationView(
            type="tui.attachment.failure",
            renderable=FragmentBlock(styled_block_fragments(upload_failure_block(
                message=failure_reason,
                event=upload_state["event"],
            ))),
        ))
        mind.frontend.application.emit(ApplicationView(type="tui.gap"))
        return None

    finally:
        await mind.await_cleanup(mind.stop_anim("upload"))

    if upload_state["event"] is not None:
        mind.frontend.application.emit(ApplicationView(
            type="tui.attachment.completed",
            renderable=FragmentBlock(styled_block_fragments(
                upload_summary_block(upload_state["event"])
            )),
        ))
        mind.frontend.application.emit(ApplicationView(type="tui.gap"))

    return uploaded_attachments


if __name__ == '__main__':
    pass
