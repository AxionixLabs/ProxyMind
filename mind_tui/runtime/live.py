# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import locale
import typing
import asyncio
from collections.abc import AsyncIterator
from mind_app.mcp import McpSessionLike
from mind_app.runtime.environment.exec_env import (
    build_runtime_exec_env,
    detect_shell
)
from mind_app.runtime.support.session_policy import friendly_exception_text
from mind_app.stream_events.assistant_boundary import is_assistant_output_boundary
from mind_app.stream_events.responses_builtin import (
    consume_builtin_done,
    resolve_builtin_name
)
from mind_app.stream_state.segment import (
    SegmentTracker,
    build_sources_text
)
from mind_nova import request
from mind_nova.events import EventReport
from mind_nova.modes import RunMode
from ..events import AppEvent
from .bridge import EmitEvent
from .provider import TurnRequest
from .tools import (
    ToolOutcome,
    TuiToolRuntime
)

if typing.TYPE_CHECKING:
    from mind_app.mind_core import Mind

_END = object()


class LiveStreamProvider:
    """通过现有请求层和工具运行时生成实时应用事件。"""

    def __init__(
        self,
        mind: "Mind",
        *,
        mode: RunMode = "chat",
        access_mode: str = "safe",
        timeout: float = 60.0
    ) -> None:
        """初始化运行时上下文和请求选项。"""
        self.mind = mind
        self.mode = mode
        self.access_mode = str(access_mode or "safe")
        self.timeout = max(1.0, float(timeout))

    def stream(self, request_data: TurnRequest) -> AsyncIterator[AppEvent]:
        """返回一轮真实输入对应的异步事件流。"""
        return self._stream(request_data)

    async def _stream(self, request_data: TurnRequest) -> AsyncIterator[AppEvent]:
        """在生产任务和界面消费者之间传递事件。"""
        queue: asyncio.Queue[AppEvent | object] = asyncio.Queue(maxsize=128)

        async def emit(event: AppEvent) -> None:
            await queue.put(event)

        async def produce() -> None:
            try:
                if request_data.shell_mode:
                    await self._run_shell(request_data, emit)
                else:
                    await self._run_model(request_data, emit)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await emit(
                    AppEvent(
                        "turn.failed",
                        text=friendly_exception_text(exc),
                        payload={"error": friendly_exception_text(exc)}
                    )
                )
            finally:
                await queue.put(_END)

        producer = asyncio.create_task(produce())
        try:
            while True:
                item = await queue.get()
                if item is _END:
                    break
                if isinstance(item, AppEvent):
                    yield item
            await producer
        finally:
            if not producer.done():
                producer.cancel()
            await asyncio.gather(producer, return_exceptions=True)

    async def _run_model(
        self,
        request_data: TurnRequest,
        emit: EmitEvent
    ) -> None:
        """建立会话和工具上下文并执行模型轮次。"""
        pref_config = await self.mind.fresh_pref_config(ttl_sec=0.0)
        metadata = self.mind.begin_session(
            title=request_data.message,
            source="tui"
        )
        report = EventReport(
            self.mode,
            metadata["cid"],
            metadata["sid"]
        )
        await report.open()
        turn_id = report.begin_turn()

        async def run_with_session(
            session: McpSessionLike,
            tools: list[dict[str, typing.Any]]
        ) -> None:
            await self._consume_model_stream(
                request_data,
                session=session,
                tools=tools,
                pref_config=pref_config,
                metadata=metadata,
                report=report,
                turn_id=turn_id,
                emit=emit
            )

        try:
            await self.mind.with_mcp_session(pref_config, run_with_session)
        finally:
            await _await_cleanup(_close_report(report))

    async def _consume_model_stream(
        self,
        request_data: TurnRequest,
        *,
        session: McpSessionLike,
        tools: list[dict[str, typing.Any]],
        pref_config: dict[str, typing.Any],
        metadata: dict[str, typing.Any],
        report: EventReport,
        turn_id: str,
        emit: EmitEvent
    ) -> None:
        """消费远程事件并调度审批与本地工具。"""
        tracker = SegmentTracker()
        tool_runtime = TuiToolRuntime(
            mind=self.mind,
            session=session,
            tools=tools,
            pref_config=pref_config,
            emit=emit
        )
        service_env = (
            self.mind.service_exec_env_snapshot()
            if self.mind.is_service_mcp_linked()
            else None
        )
        exec_env = build_runtime_exec_env(service_exec_env=service_env)

        async for event in request.stream_chat(
            self.mode,
            pref_config,
            request_data.message,
            tools,
            timeout=self.timeout,
            access_mode=self.access_mode,
            metadata=metadata,
            turn_id=turn_id,
            exec_env=exec_env
        ):
            report.bind_event(event)
            event_type = str(event.get("type") or "")

            if is_assistant_output_boundary(event_type, event):
                tracker.commit_assistant_output()

            if event_type == "turn.start":
                await emit(AppEvent("turn.start", payload=dict(event)))
                continue
            if event_type == "turn.thinking":
                await emit(AppEvent("turn.thinking", payload=dict(event)))
                continue
            if event_type == "turn.failed":
                error = str(event.get("error") or "unknown error")
                await emit(AppEvent("turn.failed", text=error, payload=dict(event)))
                return
            if event_type == "text.delta":
                tracker.on_text_delta(event)
                await emit(
                    AppEvent(
                        "text.delta",
                        text=str(event.get("text") or ""),
                        payload=dict(event)
                    )
                )
                continue
            if event_type == "text.done":
                tracker.on_text_done(event)
                await emit(AppEvent("text.done", payload=dict(event)))
                continue
            if event_type == "text.meta":
                tracker.on_text_meta(event)
                await emit(AppEvent("text.meta", payload=dict(event)))
                continue
            if event_type == "tool.builtin.call":
                payload = dict(event)
                payload["name"] = resolve_builtin_name(event)
                await emit(AppEvent("tool.builtin.call", payload=payload))
                continue
            if event_type == "tool.builtin.done":
                consume_builtin_done(event, tracker)
                payload = dict(event)
                payload["name"] = resolve_builtin_name(event)
                await emit(AppEvent("tool.builtin.done", payload=payload))
                continue
            if event_type == "tool.calls.start":
                await emit(AppEvent("tool.calls.start", payload=dict(event)))
                continue
            if event_type == "tool.calls.done":
                await emit(AppEvent("tool.calls.done", payload=dict(event)))
                continue
            if event_type == "tool.approval_required":
                await tool_runtime.handle_approval(event)
                continue
            if event_type == "tool.call":
                await emit(AppEvent("tool.call", payload=dict(event)))
                outcome = await tool_runtime.execute_call(event)
                if outcome is not None:
                    await emit(_tool_output_event(outcome))
                continue
            if event_type == "tool.output":
                outcome = tool_runtime.server_output(event)
                if outcome is not None:
                    await emit(_tool_output_event(outcome))
                continue
            if event_type == "turn.done":
                latest = tracker.latest_assistant_output_text()
                if latest:
                    self.mind.remember_last_assistant_reply(latest)
                sources = build_sources_text(tracker)
                if sources:
                    await emit(
                        AppEvent(
                            "display.block",
                            text=sources,
                            payload={"kind": "assistant"}
                        )
                    )
                await emit(AppEvent("turn.done", payload=dict(event)))
                return
            if display_text := _lifecycle_display_text(event):
                await emit(
                    AppEvent(
                        "lifecycle.display",
                        payload={"message": display_text}
                    )
                )

    async def _run_shell(
        self,
        request_data: TurnRequest,
        emit: EmitEvent
    ) -> None:
        """通过当前平台默认 shell 执行显式输入的命令。"""
        shell = detect_shell()
        prefix = shell.get("prefix")
        if not isinstance(prefix, list) or not prefix:
            raise RuntimeError("shell executable is unavailable")

        await emit(AppEvent("turn.start"))
        await emit(
            AppEvent(
                "tool.call",
                payload={
                    "name": "shell_command",
                    "arguments": {"command": request_data.message}
                }
            )
        )
        process = await asyncio.create_subprocess_exec(
            *(str(item) for item in prefix),
            request_data.message,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT
        )
        try:
            stdout, _ = await process.communicate()
        except asyncio.CancelledError:
            process.kill()
            await process.wait()
            raise

        output = _decode_output(stdout).rstrip() or "Completed without output."
        await emit(
            AppEvent(
                "tool.output",
                payload={
                    "name": "shell_command",
                    "output": output,
                    "ok": process.returncode == 0,
                    "exit_code": process.returncode
                }
            )
        )
        await emit(AppEvent("turn.done"))


def _tool_output_event(outcome: ToolOutcome) -> AppEvent:
    """把工具执行结果转换为界面事件。"""
    return AppEvent(
        "tool.output",
        payload={
            "name": outcome.name,
            "arguments": outcome.arguments,
            "ok": outcome.ok,
            "output": outcome.text,
            "fields": outcome.fields
        }
    )


def _lifecycle_display_text(event: dict[str, typing.Any]) -> str:
    """从生命周期事件中提取显式展示文本。"""
    display = event.get("display")
    if not isinstance(display, dict):
        return ""
    for key in ("text", "message", "summary"):
        value = display.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _decode_output(data: bytes | None) -> str:
    """使用 UTF-8 和当前系统编码解析命令输出。"""
    raw = data or b""
    for encoding in ("utf-8", locale.getpreferredencoding(False)):
        try:
            return raw.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace")


async def _close_report(report: EventReport) -> None:
    """刷新并关闭单轮事件上报器。"""
    await report.flush()
    await report.close()


async def _await_cleanup(awaitable: typing.Awaitable[None]) -> None:
    """在轮次取消时仍等待关键清理任务完成。"""
    task = asyncio.ensure_future(awaitable)
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


if __name__ == '__main__':
    pass
