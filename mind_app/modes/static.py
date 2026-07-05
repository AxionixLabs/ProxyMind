# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
import asyncio
from mind_app.mcp import McpSessionLike
from engine.enhance import exchange_arguments
from mind_nova.events import EventReport
from mind_nova import (
    craft, request
)
from ..stream_ui import StreamUI
from ..runtime.support.loop_support import finish_failure
from ..runtime.support.session_policy import friendly_exception_text
from ..runtime.tools.run import run_tool_step
from ..runtime.tools.display import (
    show_tool_result,
    show_tool_start
)
from ..stream_events.finish import finish_stream

if typing.TYPE_CHECKING:
    from ..mind_core import Mind


async def static_looper(
    mind: "Mind",
    session: McpSessionLike,
    mode: typing.Literal["plan"],
    pref_config: dict[str, typing.Any],
    message: str,
    tools: list[dict[str, typing.Any]],
    *_,
    **kwargs
) -> None:
    """静态编排执行器：处理 plan 生成、步骤执行和结果回写。"""
    ev_report: typing.Optional[EventReport] = kwargs.pop("ev_report", None)

    slog: StreamUI = StreamUI(mind.report.log_papers, design_level=mind.level)
    interrupted = False

    try:
        await slog.open()

        if ev_report:
            ev_report.begin_turn(round_no=1)

        final_loop_count: int = 1
        first_frame: bool     = True

        async for plan in request.stream_plan(
            mode,
            pref_config,
            message,
            tools,
            None,
            **kwargs
        ):
            if ev_report:
                ev_report.bind_event(plan)

            if first_frame:
                await mind.stop_anim()
                first_frame = False

            event_type = str(plan.get("type") or "")

            if event_type == "plan.start":
                await slog.begin_reply_wait_status()
                continue

            if event_type == "plan.failed":
                error = str(plan.get("error") or "plan request failed")
                await finish_failure(slog, ev_report, phase="plan.failed", error=error)
                continue

            if event_type != "plan.result":
                continue

            await slog.end_status()

            if not isinstance(result := plan.get("result"), dict):
                await finish_failure(
                    slog,
                    ev_report,
                    phase="plan.failed",
                    error="plan.result missing result payload"
                )
                continue

            result_type = str(result.get("type") or "")
            if result_type == "error":
                error = str(result.get("reasoning") or result.get("goal") or "plan unavailable")
                await finish_failure(slog, ev_report, phase="plan.failed", error=error)
                continue

            steps = result.get("steps")
            if not isinstance(steps, list):
                await finish_failure(
                    slog,
                    ev_report,
                    phase="plan.failed",
                    error="plan.result missing executable steps"
                )
                continue

            loop_count = result.get("loop_count")
            if not isinstance(loop_count, int):
                await finish_failure(
                    slog,
                    ev_report,
                    phase="plan.failed",
                    error="plan.result invalid loop_count"
                )
                continue

            reasoning = result.get("reasoning") or ""
            final_loop_count = loop_count

            await slog.feed(reasoning, display=StreamUI.BLOCK)
            if ev_report: ev_report.emit({
                "type"       : "exec.start",
                "loop_count" : loop_count,
                "ts"         : time.time()
            })

            for index, _ in enumerate(range(loop_count), start=1):
                if ev_report: ev_report.set_round(index)
                if ev_report: ev_report.emit({
                    "type"  : "exec.loop.start",
                    "run"   : index,
                    "total" : loop_count,
                    "ts"    : time.time()
                })

                for step_idx, step in enumerate(steps, start=1):
                    action = step["action"]

                    name, arguments = action["action"], action["args"]

                    action_meta       = action.get("meta") if isinstance(action.get("meta"), dict) else None
                    display_arguments = arguments if isinstance(arguments, dict) else {}

                    if ev_report: ev_report.emit({
                        "type"  : "exec.step.start",
                        "run"   : index,
                        "index" : step_idx,
                        "total" : len(steps),
                        "name"  : name,
                        "args"  : arguments,
                        "ts"    : time.time()
                    })

                    call_id = craft.short_uid()
                    await show_tool_start(
                        slog,
                        name,
                        display_arguments,
                        call_id=call_id
                    )

                    arguments = exchange_arguments(name, arguments, mind.report)

                    if ev_report: ev_report.emit({
                        "type"      : "exec.tool.call",
                        "call_id"   : call_id,
                        "name"      : name,
                        "arguments" : arguments,
                        "ts"        : time.time()
                    })

                    tool_run = await run_tool_step(
                        session,
                        stream_ui=slog,
                        tools=tools,
                        name=name,
                        arguments=arguments,
                        meta=action_meta,
                        mode=mode,
                        pref_config=pref_config,
                        metadata=kwargs.get("metadata") or {}
                    )

                    ok     = tool_run.ok
                    fields = tool_run.fields

                    if ev_report: ev_report.emit({
                        "type"    : "exec.tool.output",
                        "call_id" : call_id,
                        "name"    : name,
                        "ok"      : ok,
                        "result"  : fields,
                        "cost_ms" : tool_run.cost_ms,
                        "ts"      : time.time()
                    })

                    if not ok:
                        await finish_failure(
                            slog,
                            ev_report,
                            phase="exec.failed",
                            run=index,
                            index=step_idx,
                            name=name,
                            error=tool_run.text or "step failed"
                        )
                        continue

                    await show_tool_result(
                        slog,
                        name,
                        arguments,
                        tool_run
                    )
                    if ev_report: ev_report.emit({
                        "type"    : "exec.step.done",
                        "run"     : index,
                        "index"   : step_idx,
                        "total"   : len(steps),
                        "name"    : name,
                        "cost_ms" : tool_run.cost_ms,
                        "ts"      : time.time()
                    })

                if ev_report: ev_report.emit({
                    "type"  : "exec.loop.done",
                    "run"   : index,
                    "total" : loop_count,
                    "ts"    : time.time()
                })
        await finish_stream(
            ev_report, phase="exec.done", status="completed", loop_count=final_loop_count
        )
    except asyncio.CancelledError:
        interrupted = True
        raise

    except Exception as exc:
        error = friendly_exception_text(exc)
        await mind.await_cleanup(mind.stop_anim())
        await finish_failure(slog, ev_report, phase="plan.failed", error=error)

    finally:
        await mind.await_cleanup(slog.stop(blink=not interrupted))


if __name__ == '__main__':
    pass
