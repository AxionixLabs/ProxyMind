# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import sys
import typing
import asyncio
from engine.errors import MindError
from engine.signals import (
    SignalHandler,
    install_handler
)
from mind_app.cli.entry import main as _main
from mind_app.frontend.contracts import (
    ApplicationSink,
    ApplicationView
)
from mind_app.frontend.sinks import (
    ConsoleApplicationSink,
    JsonApplicationSink
)


def json_output_requested(arguments: typing.Iterable[str] | None = None) -> bool:
    """判断当前命令是否请求逐行 JSON 输出。"""
    values = sys.argv[1:] if arguments is None else arguments
    return "--json" in values


def emit_json_failure(error: typing.Any, *, phase: str) -> None:
    """向标准输出写出一个结构化失败事件。"""
    JsonApplicationSink(sys.stdout).emit(ApplicationView(
        type="json",
        renderable={
            "type": "turn.failed",
            "error": str(error),
            "phase": str(phase or "runtime"),
        },
    ))


def entry_application(json_output: bool) -> ApplicationSink:
    """创建兼容入口使用的应用级输出端。"""
    if json_output:
        return JsonApplicationSink(sys.stdout)
    return ConsoleApplicationSink()


def emit_entry_failure(
    application: ApplicationSink,
    error: typing.Any,
    *,
    phase: str,
    json_output: bool,
) -> None:
    """通过应用级输出端发送入口失败。"""
    if json_output:
        application.emit(ApplicationView(
            type="json",
            renderable={
                "type": "turn.failed",
                "error": str(error),
                "phase": str(phase or "runtime"),
            },
        ))
        return None
    application.emit(ApplicationView(type="error", renderable=str(error)))


def emit_entry_interruption(
    application: ApplicationSink,
    *,
    json_output: bool,
) -> None:
    """通过应用级输出端发送入口中断。"""
    if not json_output:
        return None
    emit_entry_failure(
        application,
        "interrupted",
        phase="interrupt",
        json_output=True,
    )


async def main(handler: SignalHandler | None = None) -> int:
    """兼容入口：转交到 `mind_app` 的应用入口。"""
    return await _main(entry_file=__file__, handler=handler)


if __name__ == "__main__":
    main_loop           = asyncio.new_event_loop()
    json_output_enabled = json_output_requested()
    entry_sink          = entry_application(json_output_enabled)

    main_task: asyncio.Task[int] | None = None

    signal_handler = install_handler(lambda: main_task)

    try:
        asyncio.set_event_loop(main_loop)
        main_task = main_loop.create_task(main(signal_handler))
        exit_code = main_loop.run_until_complete(main_task)

    except MindError as _error:
        emit_entry_failure(
            entry_sink,
            _error,
            phase="runtime",
            json_output=json_output_enabled,
        )
        entry_sink.emit(ApplicationView(type="outro"))
        sys.exit(1)

    except KeyboardInterrupt:
        if main_task is not None and not main_task.done():
            main_task.cancel()
            try:
                main_loop.run_until_complete(main_task)
            except (asyncio.CancelledError, KeyboardInterrupt):
                pass
        emit_entry_interruption(
            entry_sink,
            json_output=json_output_enabled,
        )
        entry_sink.emit(ApplicationView(type="outro"))
        sys.exit(130)

    except asyncio.CancelledError:
        emit_entry_interruption(
            entry_sink,
            json_output=json_output_enabled,
        )
        entry_sink.emit(ApplicationView(type="outro"))
        sys.exit(130)

    else:
        entry_sink.emit(ApplicationView(type="outro"))
        sys.exit(int(exit_code))
