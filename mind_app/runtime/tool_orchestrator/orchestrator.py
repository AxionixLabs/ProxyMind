# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
import asyncio
from loguru import logger
from mind_app.mcp import McpSessionLike
from mind_app.stream_ui import StreamUI
from mind_app.runtime.tool_run import run_tool_step
from mind_app.runtime.tool_display import show_tool_result
from .models import (
    InFlightTool, ToolCall, ToolOutput
)
from .policy import supports_parallel
from .rwlock import AsyncRWLock
from .sink import ToolResultSink


class ToolOrchestrator:
    """工具调用编排层，负责排队、执行锁、结果校验与统一回填。"""

    def __init__(
        self,
        *,
        session: McpSessionLike,
        stream_ui: StreamUI,
        tools: list[dict[str, typing.Any]],
        mode: str,
        pref_config: dict[str, typing.Any],
        metadata: dict[str, typing.Any],
        sink: ToolResultSink | None = None
    ) -> None:
        """初始化工具编排所需的会话、展示层、工具表和回填出口。"""

        self.session     = session
        self.stream_ui   = stream_ui
        self.tools       = tools
        self.mode        = mode
        self.pref_config = pref_config
        self.metadata    = metadata

        self.sink: ToolResultSink = sink or ToolResultSink()
        self.in_flight: dict[str, InFlightTool] = {}

        self._execution_lock: AsyncRWLock = AsyncRWLock()

    async def submit(
        self,
        call: ToolCall,
        *,
        use_coding_trace: bool = False,
        enable_progress_notify: bool = False,
        stream_callback: typing.Optional[typing.Callable[[str], typing.Awaitable[None]]] = None,
        status_text: str | None = None,
        code_status: bool = False
    ) -> bool | None:
        """提交一个工具调用并加入 in-flight 队列，返回是否允许并行。"""
        if not str(call.call_id or "").strip():
            logger.warning(f"tool orchestration rejected missing call_id name={call.name}")
            return None

        if call.call_id in self.in_flight:
            output = self._failure_output(
                call_id=call.call_id,
                text=f"duplicate tool call_id: {call.call_id}",
                data={"error": "duplicate tool call_id"}
            )
            await self.sink.post(call, output)
            return None

        parallel = supports_parallel(call.name, call.meta)

        task = asyncio.create_task(
            self._run_call(
                call,
                parallel_enabled=parallel,
                use_coding_trace=use_coding_trace,
                enable_progress_notify=enable_progress_notify,
                stream_callback=stream_callback,
                status_text=status_text,
                code_status=code_status,
            ),
            name=f"tool-call-{call.call_id}"
        )
        self.in_flight[call.call_id] = InFlightTool(
            call=call,
            task=task
        )
        return parallel

    async def drain_ready(self, *, wait: bool = False) -> list[ToolOutput]:
        """回填已完成工具；wait=True 时等待至少一个任务完成。"""
        if wait and self.in_flight and not any(item.task.done() for item in self.in_flight.values()):
            await asyncio.wait(
                [item.task for item in self.in_flight.values()],
                return_when=asyncio.FIRST_COMPLETED
            )

        ready_call_ids = [
            call_id
            for call_id, item in self.in_flight.items()
            if item.task.done()
        ]

        outputs: list[ToolOutput] = []
        for call_id in ready_call_ids:
            item   = self.in_flight.pop(call_id)
            output = await self._task_output(item)

            if output.call_id != item.call.call_id:
                logger.warning(
                    f"tool output call_id mismatch expected={item.call.call_id} actual={output.call_id}"
                )
                output = ToolOutput(
                    call_id=item.call.call_id,
                    ok=False,
                    fields={
                        "ok": False,
                        "text": "tool output call_id mismatch",
                        "data": {
                            "expected": item.call.call_id,
                            "actual": output.call_id,
                        },
                    },
                    text="tool output call_id mismatch",
                    cost_ms=output.cost_ms,
                )
            await self.sink.post(item.call, output)
            outputs.append(output)

        return outputs

    async def drain_in_flight(self) -> list[ToolOutput]:
        """等待并回填全部未完成工具。"""
        outputs: list[ToolOutput] = []
        while self.in_flight:
            outputs.extend(await self.drain_ready(wait=True))
        return outputs

    async def abort_in_flight(self, reason: str = "aborted") -> list[ToolOutput]:
        """取消全部未完成工具，并回填 aborted 结果。"""
        items = list(self.in_flight.values())
        self.in_flight.clear()

        for item in items:
            if not item.task.done():
                item.task.cancel()

        if items:
            await asyncio.gather(
                *(item.task for item in items),
                return_exceptions=True
            )

        outputs: list[ToolOutput] = []
        for item in items:
            output = self._failure_output(
                call_id=item.call.call_id,
                text=f"tool call aborted: {reason}",
                data={
                    "error": "aborted",
                    "reason": reason,
                },
                cost_ms=int((time.time() - item.started_at) * 1000)
            )
            await self.sink.post(item.call, output)
            outputs.append(output)

        return outputs

    async def _run_call(
        self,
        call: ToolCall,
        *,
        parallel_enabled: bool,
        use_coding_trace: bool,
        enable_progress_notify: bool,
        stream_callback: typing.Optional[typing.Callable[[str], typing.Awaitable[None]]],
        status_text: str | None,
        code_status: bool,
    ) -> ToolOutput:
        """按并行策略获取执行锁，并执行单个工具调用。"""
        lock_ctx = self._execution_lock.read() if parallel_enabled else self._execution_lock.write()
        async with lock_ctx:
            return await self._execute_call(
                call,
                use_coding_trace=use_coding_trace,
                enable_progress_notify=enable_progress_notify,
                stream_callback=stream_callback,
                status_text=status_text,
                code_status=code_status,
            )

    async def _execute_call(
        self,
        call: ToolCall,
        *,
        use_coding_trace: bool,
        enable_progress_notify: bool,
        stream_callback: typing.Optional[typing.Callable[[str], typing.Awaitable[None]]],
        status_text: str | None,
        code_status: bool,
    ) -> ToolOutput:
        """执行单个工具并转换为统一工具输出。"""
        started_at = time.time()
        try:
            tool_run = await run_tool_step(
                self.session,
                stream_ui=self.stream_ui,
                tools=self.tools,
                name=call.name,
                arguments=call.arguments,
                meta=call.meta,
                mode=typing.cast(typing.Any, self.mode),
                pref_config=self.pref_config,
                metadata=self.metadata,
                enable_progress_notify=enable_progress_notify,
                stream_callback=stream_callback,
                status_text=status_text,
                code_status=code_status
            )

            await show_tool_result(
                self.stream_ui,
                call.name,
                call.arguments,
                tool_run,
                ok=tool_run.ok,
                fields=tool_run.fields,
                text=tool_run.text,
                use_coding_trace=use_coding_trace
            )

            return ToolOutput(
                call_id=call.call_id,
                ok=tool_run.ok,
                fields=tool_run.fields,
                text=tool_run.text,
                cost_ms=tool_run.cost_ms
            )
        except Exception as exc:
            text = f"{type(exc).__name__}: {exc}"
            logger.warning(f"tool call failed call_id={call.call_id} name={call.name} error={text}")
            return self._failure_output(
                call_id=call.call_id,
                text=text,
                data={"error": text},
                cost_ms=int((time.time() - started_at) * 1000)
            )

    @staticmethod
    async def _task_output(item: InFlightTool) -> ToolOutput:
        """读取任务结果，失败时转换为可回填输出。"""
        try:
            return await item.task
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            text = f"{type(exc).__name__}: {exc}"
            return ToolOrchestrator._failure_output(
                call_id=item.call.call_id,
                text=text,
                data={"error": text},
                cost_ms=int((time.time() - item.started_at) * 1000)
            )

    @staticmethod
    def _failure_output(
        *,
        call_id: str,
        text: str,
        data: dict[str, typing.Any],
        cost_ms: int = 0
    ) -> ToolOutput:
        """构造失败工具输出。"""
        return ToolOutput(
            call_id=call_id,
            ok=False,
            fields={
                "ok": False,
                "text": text,
                "data": data,
            },
            text=text,
            cost_ms=cost_ms
        )


if __name__ == '__main__':
    pass
