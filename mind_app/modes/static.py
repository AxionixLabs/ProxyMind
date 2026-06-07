# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
import asyncio
from mind_app.mcp import McpSessionLike
from engine.enhancer import Enhancer
from engine.tinker import Tooling
from mind_nova.events import EventReport
from mind_nova import (
    craft, request
)
from ..stream_ui import StreamUI
from ..runtime.loop_support import (
    ensure_wakeup, finish_failure
)
from ..runtime.tool_run import run_tool_step
from ..stream_events.finish import finish_stream

if typing.TYPE_CHECKING:
    from ..mind_core import Mind


async def static_looper(
    mind: "Mind",
    session: McpSessionLike,
    mode: typing.Literal["plan"],
    pref_config: dict[str, typing.Any],
    message: str,
    openai_tools: list[dict[str, typing.Any]],
    tool_meta: dict[str, dict[str, typing.Any]],
    *_,
    **kwargs
) -> None:
    """静态编排执行器：处理 plan 生成、步骤执行和结果回写。"""

    exclude = [
        {"domain": "common", "class": "security"},
        {"domain": "common", "class": "runtime", "name": "loop_steps"},
        {"domain": "device", "class": "ui", "name": "heal_element"},
        {"domain": "bench", "class": "nexus"},
        {"domain": "bench", "class": "k6"},
        {"domain": "coding", "class": "session"}
    ]
    filtered_tools = Tooling.filter_tools(openai_tools, tool_meta, exclude=exclude)
    ev_report: typing.Optional[EventReport] = kwargs.pop("ev_report", None)

    slog: StreamUI = StreamUI(mind.report.log_papers)
    interrupted = False

    try:
        await slog.open()
        result = await session.call_tool("refresh", {"ttl_sec": mind.ttl_sec})
        extras = None if result.isError else {"devices": result.content[0].text}

        if ev_report:
            ev_report.begin_turn(round_no=1)

        context: dict[str, typing.Any] = {
            "goal"       : message,
            "mode"       : mode,
            "reasoning"  : "",
            "loop_count" : 1,
            "metadata"   : kwargs.get("metadata") or {},
            "steps"      : [],
            "current"    : None
        }

        first_frame = True

        async for plan in request.stream_plan(mode, pref_config, message, filtered_tools, extras, **kwargs):
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

            context["reasoning"]  = reasoning
            context["loop_count"] = loop_count

            await slog.feed(f"{reasoning}\n", display=StreamUI.BLOCK)
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
                    action_meta = action.get("meta") if isinstance(action.get("meta"), dict) else None
                    summary = Tooling.summarize_tool_arguments(name, arguments)

                    step_context: dict[str, typing.Any] = {
                        "run"     : index,
                        "index"   : step_idx,
                        "total"   : len(steps),
                        "name"    : name,
                        "args"    : arguments,
                        "ok"      : None,
                        "text"    : "",
                        "data"    : None,
                        "cost_ms" : 0
                    }
                    context["current"] = step_context

                    if ev_report: ev_report.emit({
                        "type"  : "exec.step.start",
                        "run"   : index,
                        "index" : step_idx,
                        "total" : len(steps),
                        "name"  : name,
                        "args"  : arguments,
                        "ts"    : time.time()
                    })

                    if error := await ensure_wakeup(
                        mind,
                        session,
                        slog,
                        tool_meta=tool_meta,
                        name=name,
                        meta=action_meta
                    ):
                        await finish_failure(
                            slog,
                            ev_report,
                            phase="exec.failed",
                            error=str(error),
                            run=index,
                            index=step_idx,
                            name=name
                        )
                        continue

                    await slog.feed(f"{summary}\n", display=StreamUI.BLOCK)

                    arguments = Enhancer.exchange(name, arguments, mind.report)
                    if name == "free_rule":
                        arguments = {
                            **arguments,
                            "context": {
                                **(arguments.get("context") or {}),
                                "plan": {
                                    "goal"       : context["goal"],
                                    "mode"       : context["mode"],
                                    "reasoning"  : context["reasoning"],
                                    "loop_count" : context["loop_count"],
                                    "metadata"   : context["metadata"],
                                    "steps"      : context["steps"],
                                    "current"    : context["current"]
                                }
                            }
                        }

                    call_id = craft.short_uid()
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
                        tool_meta=tool_meta,
                        name=name,
                        arguments=arguments,
                        meta=action_meta,
                        mode=mode,
                        pref_config=pref_config,
                        metadata=kwargs.get("metadata") or {}
                    )

                    ok = tool_run.ok
                    fields = tool_run.fields

                    step_context["ok"]      = ok
                    step_context["text"]    = tool_run.text
                    step_context["data"]    = tool_run.data
                    step_context["cost_ms"] = tool_run.cost_ms

                    context["steps"].append(step_context)
                    context["current"] = step_context

                    if ev_report: ev_report.emit({
                        "type"    : "exec.tool.output",
                        "call_id" : call_id,
                        "name"    : name,
                        "ok"      : ok,
                        "result"  : fields,
                        "cost_ms" : tool_run.cost_ms,
                        "ts"      : time.time()
                    })

                    data    = tool_run.data
                    data_ok = bool(data.get("ok")) if isinstance(data, dict) else False

                    if not ok or not data_ok:
                        step_context["data_ok"] = data_ok
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

                    await slog.feed(f"{tool_run.text}\n", display=StreamUI.BLOCK)
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
            ev_report, phase="exec.done", status="completed", loop_count=context["loop_count"]
        )
    except asyncio.CancelledError:
        interrupted = True
        raise

    finally:
        await mind.await_cleanup(slog.stop(blink=not interrupted))


if __name__ == '__main__':
    pass
