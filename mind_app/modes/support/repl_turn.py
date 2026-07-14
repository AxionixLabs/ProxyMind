# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from engine.tinker import MindError
from mind_app.mcp import McpSessionLike
from mind_core.design import Design
from mind_core.design.upload import UploadProgressLiveReporter
from mind_nova.events import EventReport
from mind_nova.modes import RunMode
from ...runtime.support.calling import resolve_mode_runner

if typing.TYPE_CHECKING:
    from ...mind_core import Mind


def print_turn_body_gap() -> None:
    Design.console.print()


def print_attach_gap() -> None:
    Design.console.print()


async def run_repl_model_turn(
    mind: "Mind",
    *,
    message_text: str,
    run_mode: RunMode,
    pref_config: dict[str, typing.Any],
    access_mode: str = "safe"
) -> None:
    """为单轮 REPL 输入建立 MCP 会话并执行模型流程。"""
    async def run_turn_with_session(
        session: McpSessionLike,
        tools: list[dict[str, typing.Any]],
    ) -> None:
        runner = resolve_mode_runner(mind, run_mode)

        uploaded_attachments: typing.Optional[list[dict[str, typing.Any]]] = None

        if mind.attach.has_pending_attachments():
            uploaded_attachments = await upload_pending_repl_attachments(mind)
            if uploaded_attachments is None:
                return None

        turn_metadata = mind.begin_session(title=message_text, source="repl")
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


async def upload_pending_repl_attachments(
    mind: "Mind"
) -> typing.Optional[list[dict[str, typing.Any]]]:
    """上传当前待发送附件；失败时打印错误并返回 None。"""
    attachments = mind.attach.pending_attachments_snapshot()
    reporter    = UploadProgressLiveReporter(Design.console)

    upload_state: dict[str, typing.Any] = {
        "event"       : None,
        "item_total"  : len(attachments),
        "total_bytes" : sum(int(attachment.get("size") or 0) for attachment in attachments)
    }

    async def capture_progress(event: dict[str, typing.Any]) -> None:
        reporter.last_event = dict(event)
        upload_state["event"] = dict(event)

    try:
        await mind.start_upload_anim(lambda: dict(upload_state))
        uploaded_attachments = await mind.attach.upload_pending_attachments(
            progress_callback=capture_progress
        )
    except MindError as upload_error:
        failure_reason = str(getattr(upload_error, "display_reason", "") or upload_error)
        Design.console.print(
            reporter.render_failure(message=failure_reason, event=reporter.last_event)
        )
        print_attach_gap()
        return None

    finally:
        await mind.await_cleanup(mind.stop_anim())

    if reporter.last_event is not None:
        Design.console.print(reporter.render_summary(reporter.last_event))
        print_attach_gap()

    return uploaded_attachments


if __name__ == '__main__':
    pass
