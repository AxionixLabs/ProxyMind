# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import functools
from mind_core.design import Design
from ..frontend import (
    ApplicationSink,
    ApplicationView,
    ConsoleApplicationSink,
    Frontend,
    SilentApplicationSink,
)
from ..interaction import NonInteractiveInteraction
from ..output.jsonl import create_json_output_session
from ..output.rich import create_rich_output_session
from ..output.text import create_text_output_session
from .selection import OutputMode, output_mode_uses_animation


def resolve_cli_frontend(output_mode: OutputMode) -> Frontend:
    """根据输出模式装配命令行前端。"""
    if output_mode == "tui":
        from ..tui.adapters.application import TuiApplicationSink
        from ..tui.adapters.session import create_tui_output_session
        from ..tui.core.runtime import TuiRuntime

        runtime = TuiRuntime()
        return Frontend(
            application=TuiApplicationSink(runtime),
            interaction=runtime,
            session_factory=functools.partial(
                create_tui_output_session,
                runtime=runtime,
            ),
            runtime=runtime,
        )

    session_factory = {
        "rich": create_rich_output_session,
        "text": create_text_output_session,
        "json": create_json_output_session,
    }[output_mode]
    application = (
        SilentApplicationSink()
        if output_mode == "json"
        else ConsoleApplicationSink()
    )
    if output_mode_uses_animation(output_mode) and isinstance(
        application,
        ConsoleApplicationSink,
    ):
        session_factory = functools.partial(
            session_factory,
            console=application.console,
        )
    return Frontend(
        application=application,
        interaction=NonInteractiveInteraction(),
        session_factory=session_factory,
    )


def resolve_cli_design(frontend: Frontend) -> Design:
    """为命令行前端创建共享终端控制台的设计实例。"""
    if isinstance(frontend.application, ConsoleApplicationSink):
        return Design(console=frontend.application.console)
    return Design()


def emit_runtime_update(
    application: ApplicationSink,
    local: dict[str, object],
    remote: dict[str, object],
) -> None:
    """发送本地运行时更新提示。"""
    application.emit(ApplicationView(
        type="runtime.update_available",
        payload={
            "local": dict(local),
            "remote": dict(remote),
        },
    ))


if __name__ == '__main__':
    pass
