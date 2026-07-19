# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from dataclasses import (
    dataclass, field
)
from engine.enhance import exchange_arguments
from mind_app.mcp import McpSessionLike
from mind_app.output import OutputPort
from mind_app.presentation.contracts import PresentationSink
from mind_nova import request
from .display import (
    show_tool_result, show_tool_start
)
from .policy import supports_parallel
from ..support.rwlock import AsyncRWLock
from .run import run_tool_step


@dataclass(slots=True)
class BatchToolResult:
    """单个 batch 工具的本地展示结果。"""
    name: str
    arguments: dict[str, typing.Any]
    ok: bool
    text: str
    cost_ms: int = 0


@dataclass(slots=True)
class PendingToolCall:
    """已下发但尚未执行的客户端工具调用。"""
    event: dict[str, typing.Any]
    name: str
    arguments: dict[str, typing.Any]
    meta: dict[str, typing.Any] | None
    execution: dict[str, typing.Any] | None
    use_coding_trace: bool


@dataclass(slots=True)
class ToolCallBatch:
    """服务端声明的一批客户端工具调用。"""
    batch_id: str
    call_ids: list[str]
    count: int
    ready: bool
    timeout_sec: float | None
    calls: list[PendingToolCall] = field(default_factory=list)

    def contains(self, call_id: str) -> bool:
        return not self.call_ids or call_id in self.call_ids


def batch_from_event(event: dict[str, typing.Any]) -> ToolCallBatch:
    """从 tool.calls.start 事件构造 batch 状态。"""
    raw_call_ids = event.get("call_ids")

    call_ids = [
        str(item) for item in raw_call_ids
        if str(item or "").strip()
    ] if isinstance(raw_call_ids, list) else []

    raw_count = event.get("count")
    count     = int(raw_count) if isinstance(raw_count, (int, float)) else len(call_ids)

    raw_timeout = event.get("timeout_sec")
    timeout_sec = float(raw_timeout) if isinstance(raw_timeout, (int, float)) else None

    return ToolCallBatch(
        batch_id=str(event.get("batch_id") or ""),
        call_ids=call_ids,
        count=count,
        ready=bool(event.get("ready", False)),
        timeout_sec=timeout_sec
    )


class ToolBatchExecutor:
    """客户端工具 batch 执行器，隔离并发调度和结果回填细节。"""

    def __init__(
        self,
        *,
        session: McpSessionLike,
        stream_ui: OutputPort,
        presentation: PresentationSink,
        tools: list[dict[str, typing.Any]],
        mode: str,
        pref_config: dict[str, typing.Any],
        metadata: dict[str, typing.Any],
        report: typing.Any
    ) -> None:
        self.session      = session
        self.stream_ui    = stream_ui
        self.presentation = presentation
        self.tools        = tools
        self.mode         = mode
        self.pref_config  = pref_config
        self.metadata     = metadata
        self.report       = report

    @staticmethod
    async def post_tool_result(
        event: dict[str, typing.Any],
        name: str,
        ok: bool,
        result: typing.Union[
            None,
            str,
            int,
            bool,
            float,
            list[typing.Any],
            dict[str, typing.Any]
        ],
        execution: dict[str, typing.Any] | None
    ) -> dict[str, typing.Any]:
        """回填工具结果并返回服务端状态。"""
        return await request.post_tool_result(
            event["cid"],
            event["sid"],
            event["call_id"],
            name,
            ok,
            result,
            execution=execution
        )

    async def execute_call(
        self,
        pending: PendingToolCall,
        *,
        display: bool = True
    ) -> BatchToolResult:
        """执行单个客户端工具并回填结果。"""
        event            = pending.event
        name             = pending.name
        arguments        = dict(pending.arguments)
        event_execution  = pending.execution
        use_coding_trace = pending.use_coding_trace
        cost_ms          = 0

        try:
            if display:
                self.stream_ui.record_tool_arguments(
                    name,
                    arguments,
                    call_id=str(event.get("call_id") or "")
                )
                if not use_coding_trace:
                    await show_tool_start(
                        self.presentation,
                        name,
                        arguments
                    )

            arguments = exchange_arguments(name, arguments, self.report)

            tool_run = await run_tool_step(
                self.session,
                stream_ui=self.stream_ui,
                presentation=self.presentation,
                tools=self.tools,
                name=name,
                arguments=arguments,
                meta=pending.meta,
                pref_config=self.pref_config,
                enable_progress_notify=True,
                status_text=None,
                execution=event_execution,
                cid=str(event.get("cid") or ""),
                sid=str(event.get("sid") or ""),
                call_id=str(event.get("call_id") or ""),
            )

            ok      = tool_run.ok
            fields  = tool_run.fields
            text    = tool_run.text
            cost_ms = tool_run.cost_ms

            if display:
                if use_coding_trace:
                    await self.stream_ui.end_status()
                await show_tool_result(
                    self.presentation,
                    name,
                    arguments,
                    tool_run,
                    ok=ok,
                    text=text,
                    use_coding_trace=use_coding_trace
                )

        except Exception as exc:
            text = f"{type(exc).__name__}: {exc}"
            ok   = False

            fields = {
                "ok"   : False,
                "text" : text,
                "data" : {"error": text}
            }

        await self.post_tool_result(
            event,
            name,
            ok,
            fields,
            event_execution
        )
        return BatchToolResult(
            name=name,
            arguments=arguments,
            ok=ok,
            text=str(text or ""),
            cost_ms=cost_ms
        )

    async def execute_batch(
        self,
        batch: ToolCallBatch,
        *,
        display_each: bool = True
    ) -> list[BatchToolResult]:
        """按并发策略执行一批客户端工具。"""
        lock = AsyncRWLock()

        async def run_one(pending: PendingToolCall) -> BatchToolResult:
            parallel = supports_parallel(pending.name, pending.meta)
            rw_ctx   = lock.read() if parallel else lock.write()

            async with rw_ctx:
                return await self.execute_call(pending, display=display_each)

        if not batch.calls:
            return []

        return list(await asyncio.gather(*(run_one(pending) for pending in batch.calls)))


if __name__ == '__main__':
    pass
