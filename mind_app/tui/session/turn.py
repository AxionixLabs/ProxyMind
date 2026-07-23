# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_app.mcp.contracts import McpSessionLike
from mind_nova.events import EventReport
from mind_nova.modes import RunMode
from ...runtime.support.calling import resolve_mode_runner

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
        runner        = resolve_mode_runner(mind, run_mode)
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
                access_mode=access_mode,
                metadata=turn_metadata,
                ev_report=ev_report
            )

        finally:
            await ev_report.flush()
            await ev_report.close()

    await mind.with_mcp_session(pref_config, run_turn_with_session)


if __name__ == '__main__':
    pass
