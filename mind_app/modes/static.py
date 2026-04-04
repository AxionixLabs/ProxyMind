# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
import asyncio
from loguru import logger
from mcp import ClientSession
from mind_core.design import Design
from engine.enhancer import Enhancer
from engine.tinker import Tooling
from mind_nova.events import EventReport
from mind_nova import (
    craft, request
)
from .tool_result import tool_result_data, tool_result_text
from ..stream_ui import StreamUI
from ..stream_events.finish import finish_stream

if typing.TYPE_CHECKING:
    from ..mind_core import Mind


async def static_looper(
    mind: "Mind",
    session: ClientSession,
    mode: typing.Literal["plan"],
    model_api: dict[str, typing.Any],
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
        {"domain": "bench", "class": "nexus"},
        {"domain": "bench", "class": "k6"}
    ]
    filtered_tools = Tooling.filter_tools(openai_tools, tool_meta, exclude=exclude)
    ev_report: typing.Optional[EventReport] = kwargs.pop("ev_report", None)

    def emit_event(event: dict[str, typing.Any]) -> None:
        """发送 plan 运行事件，缺省时自动降级为空操作。"""
        if ev_report:
            ev_report.emit(event)

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

        async for plan in request.stream_plan(mode, model_api, message, filtered_tools, extras, **kwargs):
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
                await finish_stream(ev_report, phase="plan.failed", error=error)
                return logger.error(f"{plan}\n")

            if event_type != "plan.result":
                continue

            await slog.end_status()

            if not isinstance(result := plan.get("result"), dict):
                await finish_stream(ev_report, phase="plan.failed", error="plan.result missing result payload")
                return logger.error(f"{plan}\n")

            result_type = str(result.get("type") or "")
            if result_type == "error":
                error = str(result.get("reasoning") or result.get("goal") or "plan unavailable")
                await finish_stream(ev_report, phase="plan.failed", error=error)
                return logger.error(f"{plan}\n")

            steps = result.get("steps")
            if not isinstance(steps, list):
                logger.warning(plan)
                await finish_stream(ev_report, phase="plan.failed", error="plan.result missing executable steps")
                return logger.error(f"{plan}\n")

            loop_count = result.get("loop_count")
            if not isinstance(loop_count, int):
                logger.warning(plan)
                await finish_stream(ev_report, phase="plan.failed", error="plan.result invalid loop_count")
                return logger.error(f"{plan}\n")

            logger.debug(f"Loop Count -> {loop_count}")
            for step in steps:
                logger.debug(step["action"])

            reasoning = result.get("reasoning") or ""

            context["reasoning"]  = reasoning
            context["loop_count"] = loop_count

            logger.info(reasoning)
            emit_event({
                "type"       : "exec.start",
                "loop_count" : loop_count,
                "ts"         : time.time()
            })

            for index, _ in enumerate(range(loop_count), start=1):
                if ev_report: ev_report.set_round(index)
                emit_event({
                    "type"  : "exec.loop.start",
                    "run"   : index,
                    "total" : loop_count,
                    "ts"    : time.time()
                })

                for step_idx, step in enumerate(steps, start=1):
                    action = step["action"]

                    name, arguments = action["action"], action["args"]
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

                    emit_event({
                        "type"  : "exec.step.start",
                        "run"   : index,
                        "index" : step_idx,
                        "total" : len(steps),
                        "name"  : name,
                        "args"  : arguments,
                        "ts"    : time.time()
                    })

                    if Tooling.needs_wakeup(tool_meta, name):
                        if error := await mind.wakeup(session):
                            await finish_stream(
                                ev_report,
                                phase="exec.failed",
                                error=str(error),
                                run=index,
                                index=step_idx,
                                name=name
                            )
                            return logger.error(f"{error}\n")

                    logger.info(summary)

                    arguments = Enhancer.exchange(name, arguments, mind.report)
                    if name == "free_rule":
                        Design.console.print()
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
                    emit_event({
                        "type"      : "exec.tool.call",
                        "call_id"   : call_id,
                        "name"      : name,
                        "arguments" : arguments,
                        "ts"        : time.time()
                    })

                    started_at = time.time()

                    await slog.begin_tool_status()
                    try:
                        result = await session.call_tool(name, arguments)
                        ok = not result.isError

                        enhancer: Enhancer = Enhancer(session, mode, model_api, kwargs.get("metadata"))
                        fields = await enhancer.enhance(name, arguments, result, ok, slog)
                    finally:
                        await slog.end_status()

                    step_context["ok"]      = ok
                    step_context["text"]    = tool_result_text(fields)
                    step_context["data"]    = tool_result_data(fields)
                    step_context["cost_ms"] = int((time.time() - started_at) * 1000)

                    context["steps"].append(step_context)
                    context["current"] = step_context

                    emit_event({
                        "type"    : "exec.tool.output",
                        "call_id" : call_id,
                        "name"    : name,
                        "ok"      : ok,
                        "result"  : fields,
                        "cost_ms" : int((time.time() - started_at) * 1000),
                        "ts"      : time.time()
                    })

                    data    = tool_result_data(fields)
                    data_ok = bool(data.get("ok")) if isinstance(data, dict) else False

                    if not ok or not data_ok:
                        step_context["data_ok"] = data_ok
                        brief_err = tool_result_text(fields) or "step failed"
                        await finish_stream(
                            ev_report,
                            phase="exec.failed",
                            run=index,
                            index=step_idx,
                            name=name,
                            error=brief_err
                        )
                        return logger.error(f"{fields}\n")

                    logger.info(tool_result_text(fields))
                    emit_event({
                        "type"    : "exec.step.done",
                        "run"     : index,
                        "index"   : step_idx,
                        "total"   : len(steps),
                        "name"    : name,
                        "cost_ms" : int((time.time() - started_at) * 1000),
                        "ts"      : time.time()
                    })

                emit_event({
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
