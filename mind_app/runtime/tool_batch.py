# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from dataclasses import (
    dataclass, field
)
from engine.enhance import exchange_arguments
from mind_app.mcp import McpSessionLike
from mind_app.stream_ui import StreamUI
from mind_nova import request
from .execution_policy import should_pass_execution_to_tool
from .tool_display import (
    show_tool_result, show_tool_start
)
from .tool_policy import supports_parallel
from .rwlock import AsyncRWLock
from .tool_run import run_tool_step


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
        stream_ui: StreamUI,
        tools: list[dict[str, typing.Any]],
        mode: str,
        pref_config: dict[str, typing.Any],
        metadata: dict[str, typing.Any],
        report: typing.Any
    ) -> None:
        self.session     = session
        self.stream_ui   = stream_ui
        self.tools       = tools
        self.mode        = mode
        self.pref_config = pref_config
        self.metadata    = metadata
        self.report      = report

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

    async def execute_call(self, pending: PendingToolCall) -> None:
        """执行单个客户端工具并回填结果。"""
        event            = pending.event
        name             = pending.name
        arguments        = dict(pending.arguments)
        event_execution  = pending.execution
        use_coding_trace = pending.use_coding_trace

        try:
            if not use_coding_trace:
                await show_tool_start(
                    self.stream_ui,
                    name,
                    arguments,
                    call_id=str(event.get("call_id") or "")
                )
            else:
                self.stream_ui.record_tool_arguments(
                    name,
                    arguments,
                    call_id=str(event.get("call_id") or "")
                )

            arguments = exchange_arguments(name, arguments, self.report)
            if should_pass_execution_to_tool(name, event_execution):
                arguments = {**arguments, "execution": event_execution}

            tool_run = await run_tool_step(
                self.session,
                stream_ui=self.stream_ui,
                tools=self.tools,
                name=name,
                arguments=arguments,
                meta=pending.meta,
                mode=self.mode,
                pref_config=self.pref_config,
                metadata=self.metadata,
                enable_progress_notify=True,
                stream_callback=lambda x: self.stream_ui.feed(
                    x, display=StreamUI.BLOCK
                ),
                status_text="coding" if use_coding_trace else None,
                code_status=use_coding_trace
            )

            ok     = tool_run.ok
            fields = tool_run.fields
            text   = tool_run.text

            await show_tool_result(
                self.stream_ui,
                name,
                arguments,
                tool_run,
                ok=ok,
                fields=fields,
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

    async def execute_batch(self, batch: ToolCallBatch) -> None:
        """按并发策略执行一批客户端工具。"""
        lock = AsyncRWLock()

        async def run_one(pending: PendingToolCall) -> None:
            parallel = supports_parallel(pending.name, pending.meta)
            rw_ctx   = lock.read() if parallel else lock.write()

            async with rw_ctx:
                await self.execute_call(pending)

        if not batch.calls:
            return

        await asyncio.gather(*(run_one(pending) for pending in batch.calls))


if __name__ == '__main__':
    pass
