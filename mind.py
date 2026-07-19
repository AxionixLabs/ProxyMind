# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import sys
import json
import typing
import asyncio
from mind_core.design import Design
from engine.tinker import MindError
from engine.signals import (
    SignalHandler, install_handler
)
from mind_app.mind_entry import main as _main


def json_output_requested(arguments: typing.Iterable[str] | None = None) -> bool:
    """判断当前命令是否请求逐行 JSON 输出。"""
    values = sys.argv[1:] if arguments is None else arguments
    return "--json" in values


def emit_json_failure(error: typing.Any, *, phase: str) -> None:
    """向标准输出写出一个结构化失败事件。"""
    payload = {
        "type": "turn.failed",
        "error": str(error),
        "phase": str(phase or "runtime"),
    }
    sys.stdout.write(json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ) + "\n")
    sys.stdout.flush()


async def main(handler: SignalHandler | None = None) -> int:
    """兼容入口：转交到 `mind_app` 的应用入口。"""
    return await _main(entry_file=__file__, handler=handler)


if __name__ == "__main__":
    main_loop   = asyncio.new_event_loop()
    json_output = json_output_requested()

    main_task: asyncio.Task[int] | None = None

    signal_handler = install_handler(lambda: main_task)

    try:
        asyncio.set_event_loop(main_loop)
        main_task = main_loop.create_task(main(signal_handler))
        exit_code = main_loop.run_until_complete(main_task)

    except MindError as _error:
        if json_output:
            emit_json_failure(_error, phase="runtime")
        else:
            Design.Doc.err(_error)
            Design.show_outro()
        sys.exit(1)

    except KeyboardInterrupt:
        if main_task is not None and not main_task.done():
            main_task.cancel()
            try:
                main_loop.run_until_complete(main_task)
            except (asyncio.CancelledError, KeyboardInterrupt):
                pass
        if json_output:
            emit_json_failure("interrupted", phase="interrupt")
        else:
            Design.show_outro()
        sys.exit(130)

    except asyncio.CancelledError:
        if json_output:
            emit_json_failure("interrupted", phase="interrupt")
        else:
            Design.show_outro()
        sys.exit(130)

    else:
        if not json_output:
            Design.show_outro()
        sys.exit(int(exit_code))
