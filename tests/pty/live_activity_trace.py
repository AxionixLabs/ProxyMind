"""在真实 mind.py 会话中记录 renderer 提交，不替换模型、审批或工具。"""

import argparse
import asyncio
import contextlib
import json
import runpy
import sys
import time
import typing

import httpx

from pathlib import Path
from unittest.mock import patch

from prompt_toolkit.application import Application

from frontends.tui.core.runtime import TuiRuntime


def main() -> None:
    """转发真实 CLI 参数并把状态交接帧写入指定 JSONL。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--disconnect-stream-once", action="store_true")
    parser.add_argument(
        "--disconnect-event", choices=("turn.started", "text.delta"),
        default="turn.started",
    )
    parser.add_argument("--disconnect-delay", type=float, default=0.0)
    parser.add_argument("--replay-delay", type=float, default=0.0)
    options, arguments = parser.parse_known_args()
    if options.replay_delay < 0 or options.disconnect_delay < 0:
        parser.error("stream delays must be non-negative")
    if arguments[:1] == ["--"]:
        arguments = arguments[1:]
    entry = Path(__file__).resolve().parents[2] / "mind.py"
    original_open = TuiRuntime.open
    attached: set[TuiRuntime] = set()
    previous: dict[TuiRuntime, str] = {}
    disconnected: set[str] = set()
    delayed: set[str] = set()
    original_lines = httpx.Response.aiter_lines

    with options.trace.open("w", encoding="utf-8") as trace:
        async def lines_with_disconnect(
            response: httpx.Response,
        ) -> typing.AsyncIterator[str]:
            """在指定真实事件交付后关闭一次本进程连接，验证实际 attach/replay。"""
            async for line in original_lines(response):
                if (
                    options.replay_delay > 0
                    and not delayed
                    and response.request.url.path == "/mind-attach"
                ):
                    delayed.add("mind-attach")
                    await asyncio.sleep(options.replay_delay)
                yield line
                if (
                    not options.disconnect_stream_once
                    or disconnected
                    or response.request.url.path != "/mind-chat"
                    or not line.startswith("data:")
                ):
                    continue
                event = json.loads(line[len("data:"):])
                if (
                    not isinstance(event, dict)
                    or event.get("type") != options.disconnect_event
                ):
                    continue
                disconnected.add("mind-chat")
                await asyncio.sleep(options.disconnect_delay)
                await response.aclose()
                trace.write(json.dumps({
                    "time": time.time(), "event": "stream_closed_once",
                }) + "\n")
                trace.flush()
                return

        async def open_traced(runtime: TuiRuntime) -> None:
            """为真实 Application 附加只读帧观察器。"""
            if runtime not in attached:
                attached.add(runtime)

                def capture(application: Application[None]) -> None:
                    """只记录状态、组件身份与几何变化，避免收集正文和命令参数。"""
                    screen = application.renderer.last_rendered_screen
                    if screen is None:
                        return None
                    positions = screen.visible_windows_to_write_positions
                    status = positions.get(runtime.screen.status_window)
                    input_position = positions.get(runtime.screen.input.window)
                    lease = runtime.activity.lease("wait")
                    visible = status is not None and status.height > 0
                    state = {
                        "status_visible": visible,
                        "title": runtime.activity._turn_surface_title if visible else "",
                        "height": status.height if status is not None else 0,
                        "lease": lease.generation if lease is not None else None,
                        "started_at": runtime.activity._wait_started_at,
                        "approval_active": runtime.screen.approval.active,
                        "input_row": (
                            application.output.get_size().rows
                            - runtime.screen._visible_height()
                            + input_position.ypos
                            if input_position is not None else None
                        ),
                    }
                    signature = json.dumps(state, sort_keys=True)
                    if previous.get(runtime) == signature:
                        return None
                    previous[runtime] = signature
                    trace.write(json.dumps({
                        "time": time.time(),
                        "frame": application.render_counter,
                        **state,
                    }, ensure_ascii=False) + "\n")
                    trace.flush()

                runtime.screen.application.after_render += capture
            await original_open(runtime)

        with contextlib.ExitStack() as patches:
            patches.enter_context(patch.object(TuiRuntime, "open", open_traced))
            if options.disconnect_stream_once or options.replay_delay > 0:
                patches.enter_context(patch.object(
                    httpx.Response, "aiter_lines", lines_with_disconnect,
                ))
            sys.argv = [str(entry), *arguments]
            runpy.run_path(str(entry), run_name="__main__")


if __name__ == "__main__":
    main()
