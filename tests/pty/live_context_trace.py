"""为真实客户端上下文验收记录脱敏协议与实际渲染帧，不替换服务或模型。"""

import argparse
import asyncio
import contextlib
import hashlib
import json
import runpy
import sys
import time

import httpx

from pathlib import Path
from unittest.mock import patch
from prompt_toolkit.application import Application

from frontends.tui.core.runtime import TuiRuntime
from agent.application.views import ContextCompactionView
from frontends.tui.rendering.fragments import fragments_text
from protocol.client import chat
from protocol.client import compact
from protocol.client import session_replay


def main() -> None:
    """运行真实入口，并记录用量事件及 footer 渲染结果。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--entry", type=Path, required=True)
    parser.add_argument("--disconnect-event", choices=("context.usage.updated", "tool.call", "text.delta", "context.compaction.started"))
    parser.add_argument("--replay-delay", type=float, default=0.0)
    options, arguments = parser.parse_known_args()
    if arguments[:1] == ["--"]:
        arguments = arguments[1:]
    original_open = TuiRuntime.open
    original_usage_view = TuiRuntime.set_context_usage
    original_chat = chat.parse_stream_event
    original_compact = compact.parse_compact_event
    original_replay = session_replay.parse_stream_event
    original_lines = httpx.Response.aiter_lines
    original_send = httpx.AsyncClient.send
    original_recovery = chat.TurnEventStream._notify_recovery
    disconnected = set()
    delayed = set()
    attached = set()
    with options.trace.open("w", encoding="utf-8", buffering=1) as trace:
        def emit(value):
            """写入不含鉴权、正文或工具参数的验收事实。"""
            trace.write(json.dumps({"time": time.time(), **value}, ensure_ascii=False) + "\n")

        async def send_traced(client, request, **kwargs):
            """仅记录请求方法和端点，不采集鉴权、查询参数或正文。"""
            emit({"kind": "request", "method": request.method, "path": request.url.path})
            return await original_send(client, request, **kwargs)

        def usage_view_traced(runtime, view):
            """记录实际投影的身份、窗口和计数，检查模型切换期间的归属。"""
            record = view.record
            emit({"kind": "usage_view", "status": view.status, "record": {
                field: getattr(record, field) for field in (
                    "cid", "sid", "event_seq", "model", "route", "usage_source",
                    "model_context_window", "last_total_tokens", "total_tokens",
                )
            } if record is not None else None})
            return original_usage_view(runtime, view)

        async def recovery_traced(stream, status, **kwargs):
            """观察真实恢复状态交接，不改变水位或恢复决策。"""
            emit({"kind": "recovery", "status": status, "event_seq": kwargs.get("event_seq")})
            return await original_recovery(stream, status, **kwargs)

        async def lines_with_disconnect(response):
            """在实际事件之后关闭一次当前连接，让正式恢复逻辑重新接入。"""
            async for line in original_lines(response):
                path = response.request.url.path
                if path == "/mind-attach" and options.replay_delay > 0 and not delayed:
                    delayed.add(path)
                    await asyncio.sleep(options.replay_delay)
                yield line
                if not options.disconnect_event or disconnected or path not in {"/mind-chat", "/compact"} or not line.startswith("data:"):
                    continue
                payload = json.loads(line[len("data:"):])
                if isinstance(payload, dict) and payload.get("type") == options.disconnect_event:
                    disconnected.add(path)
                    await response.aclose()
                    emit({"kind": "disconnect", "after": options.disconnect_event})
                    return

        def observe_payload(payload, parse):
            """仅观察用量及压缩事件的公开统计字段。"""
            if payload.get("type") in {
                "context.usage.updated", "context.compaction.started",
                "context.compaction.completed", "context.compaction.failed", "turn.completed",
                "stream.gap", "review.started", "review.completed", "review.failed", "review.cancelled",
            }:
                emit({"kind": "wire", "event": {key: payload[key] for key in (
                    "type", "proto", "cid", "sid", "turn_id", "event_seq",
                    "presentation_epoch", "context_usage", "before_items", "after_items",
                    "item_status", "reason", "error_type", "status", "gap_kind", "next_seq",
                    "item_id", "latency_ms", "phase", "trigger", "retryable",
                ) if key in payload}})
            return parse(payload)

        async def open_traced(runtime: TuiRuntime) -> None:
            """在真实 Application 上挂接只读帧观察器。"""
            if runtime not in attached:
                attached.add(runtime)

                def capture(application: Application[None]) -> None:
                    """记录实际布局、光标和 footer，不重建或驱动展示状态。"""
                    screen = application.renderer.last_rendered_screen
                    if screen is None:
                        return
                    position = screen.visible_windows_to_write_positions.get(runtime.screen.input.window)
                    compaction_rows = []
                    for y, row in sorted(screen.data_buffer.items()):
                        text = "".join(row[x].char for x in sorted(row))
                        if any(phrase in text for phrase in (
                            "Context compac", "Making room", "Stopped waiting", "Lost contact",
                        )):
                            compaction_rows.append({
                                "row": application.output.get_size().rows - runtime.screen._visible_height() + y,
                                "text": text,
                            })
                    state = {
                        "kind": "frame", "footer": runtime.screen.context_usage_label,
                        "frame": application.render_counter,
                        "turn_running": runtime.execution_active,
                        "has_draft": bool(runtime.screen.input.buffer.text.strip()),
                        "context_visible": any(
                            "footer.context" in cell.style
                            for row in screen.data_buffer.values() for cell in row.values()
                        ),
                        "columns": application.output.get_size().columns,
                        "rows": application.output.get_size().rows,
                        "input_row": position.ypos if position is not None else None,
                        "input_height": position.height if position is not None else None,
                        "draft_hash": hashlib.sha256(runtime.screen.input.buffer.text.encode("utf-8")).hexdigest(),
                        "draft_cursor": runtime.screen.input.buffer.cursor_position,
                        "foreground_active": runtime.submission_deferred,
                        "activity": fragments_text(runtime.screen.activity_block.fragments) if runtime.screen.activity_block is not None else None,
                        "process_status": fragments_text(runtime.screen.process_status.fragments()),
                        "compaction_rows": compaction_rows,
                        "compaction_records": [
                            {"item_id": cell.source.item_id if isinstance(cell.source, ContextCompactionView) else "",
                             "text": fragments_text(cell.transcript_block.fragments)}
                            for cell in runtime.document.blocks
                            if "Context compact" in fragments_text(cell.transcript_block.fragments)
                        ],
                        "footer_cells": [
                            {"row": y, "column": x, "text": cell.char, "style": cell.style,
                             "dim": application._merged_style.get_attrs_for_style_str(cell.style).dim,
                             "bold": application._merged_style.get_attrs_for_style_str(cell.style).bold}
                            for y, row in screen.data_buffer.items()
                            for x, cell in row.items() if "footer.context" in cell.style
                        ],
                    }
                    emit(state)

                runtime.screen.application.after_render += capture
            await original_open(runtime)

        with contextlib.ExitStack() as patches:
            patches.enter_context(patch.object(TuiRuntime, "open", open_traced))
            patches.enter_context(patch.object(TuiRuntime, "set_context_usage", usage_view_traced))
            patches.enter_context(patch.object(httpx.AsyncClient, "send", send_traced))
            patches.enter_context(patch.object(chat.TurnEventStream, "_notify_recovery", recovery_traced))
            if options.disconnect_event:
                patches.enter_context(patch.object(httpx.Response, "aiter_lines", lines_with_disconnect))
            patches.enter_context(patch.object(chat, "parse_stream_event", lambda p: observe_payload(p, original_chat)))
            patches.enter_context(patch.object(compact, "parse_compact_event", lambda p: observe_payload(p, original_compact)))
            patches.enter_context(patch.object(session_replay, "parse_stream_event", lambda p: observe_payload(p, original_replay)))
            entry = options.entry.resolve(strict=True)
            sys.argv = [str(entry), *arguments]
            runpy.run_path(str(entry), run_name="__main__")


if __name__ == "__main__":
    main()
