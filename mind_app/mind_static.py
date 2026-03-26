# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import time
import typing
from loguru import logger
from mcp import ClientSession
from mind_core.design import Design
from engine.enhancer import Enhancer
from engine.tinker import (
    Tooling, StreamTyperLogger
)
from mind_nova.request import EventReport
from mind_nova import (
    craft, request
)

if typing.TYPE_CHECKING:
    from .mind_core import Mind


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
        {"domain": "bench", "class": "nexus"}
    ]
    filtered_tools = Tooling.filter_tools(openai_tools, tool_meta, exclude=exclude)
    event_report: typing.Optional[EventReport] = kwargs.pop("ev_report", None)
    plan_protocol_types = {"plan.start", "plan.result", "plan.done", "plan.failed"}

    def emit_event(event: dict[str, typing.Any]) -> None:
        """发送 plan 运行事件，缺省时自动降级为空操作。"""
        if event_report:
            event_report.emit(event)

    async def finish(
        status: typing.Literal["completed", "failed"],
        *,
        emit_exec_terminal: bool,
        **extra
    ) -> None:
        """统一结束编排执行，并在返回前刷完事件。"""
        if not event_report:
            return None
        if emit_exec_terminal and status == "completed":
            event_report.emit({
                "type"   : "exec.done",
                "status" : "completed",
                "ts"     : time.time(),
                **extra
            })
        elif emit_exec_terminal:
            event_report.emit({
                "type"  : "exec.failed",
                "error" : str(extra.get("error") or "plan failed"),
                "ts"    : time.time(),
                **extra
            })
        await event_report.flush()

    stream_logger: StreamTyperLogger = StreamTyperLogger(mind.report.log_papers)
    refresh_result = await session.call_tool("refresh", {"ttl_sec": mind.ttl_sec})
    plan_extras = None if refresh_result.isError else {"devices": refresh_result.content[0].text}

    if event_report:
        event_report.begin_turn(round_no=1)

    runtime_context: dict[str, typing.Any] = {
        "goal"       : message,
        "mode"       : mode,
        "reasoning"  : "",
        "loop_count" : 1,
        "metadata"   : kwargs.get("metadata") or {},
        "steps"      : [],
        "current"    : None
    }

    has_plan_result: bool = False
    anim_stopped: bool = False
    execution_started: bool = False

    async for plan in request.stream_plan(mode, model_api, message, filtered_tools, plan_extras, **kwargs):
        if not anim_stopped:
            await mind.stop_anim()
            anim_stopped = True

        event_type = str(plan.get("type") or "")

        if event_report:
            event_report.bind_event(plan)
            if event_type in plan_protocol_types:
                event_report.emit(plan)

        if event_type in ["plan.start", "plan.done"]:
            continue

        if event_type == "plan.failed":
            error = str(plan.get("error") or "plan request failed")
            await finish("failed", emit_exec_terminal=False, error=error)
            return logger.error(f"{plan}\n")

        if event_type != "plan.result":
            continue

        if not isinstance(result := plan.get("result"), dict):
            await finish("failed", emit_exec_terminal=True, error="plan.result missing result payload")
            return logger.error(f"{plan}\n")

        result_type = str(result.get("type") or "")
        if result_type == "error":
            error = str(result.get("reasoning") or result.get("goal") or "plan unavailable")
            await finish("failed", emit_exec_terminal=True, error=error)
            return logger.error(f"{plan}\n")

        steps = result.get("steps")
        if not isinstance(steps, list):
            logger.warning(plan)
            await finish("failed", emit_exec_terminal=True, error="plan.result missing executable steps")
            return logger.error(f"{plan}\n")

        loop_count = result.get("loop_count")
        if not isinstance(loop_count, int):
            logger.warning(plan)
            await finish("failed", emit_exec_terminal=True, error="plan.result invalid loop_count")
            return logger.error(f"{plan}\n")

        logger.debug(f"Loop Count -> {loop_count}")
        for step in steps:
            logger.debug(step["action"])

        has_plan_result = True

        reasoning = result.get("reasoning") or ""

        runtime_context["reasoning"]  = reasoning
        runtime_context["loop_count"] = loop_count

        logger.info(reasoning)
        execution_started = True
        emit_event({
            "type"       : "exec.start",
            "loop_count" : loop_count,
            "ts"         : time.time()
        })

        for index, _ in enumerate(range(loop_count), start=1):
            if event_report:
                event_report.set_round(index)
            emit_event({"type": "exec.loop.start", "run": index, "total": loop_count, "ts": time.time()})

            for step_idx, step in enumerate(steps, start=1):
                action = step["action"]
                name, arguments = action["action"], action["args"]

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
                runtime_context["current"] = step_context

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
                        await finish(
                            "failed",
                            emit_exec_terminal=execution_started,
                            error=str(error),
                            run=index,
                            index=step_idx,
                            name=name
                        )
                        return logger.error(f"{error}\n")

                logger.info(Tooling.summarize_tool_arguments(name, arguments))

                arguments = Enhancer.exchange(name, arguments, mind.report)
                if name == "free_rule":
                    Design.console.print()
                    arguments = {
                        **arguments,
                        "context": {
                            **(arguments.get("context") or {}),
                            "plan": {
                                "goal"       : runtime_context["goal"],
                                "mode"       : runtime_context["mode"],
                                "reasoning"  : runtime_context["reasoning"],
                                "loop_count" : runtime_context["loop_count"],
                                "metadata"   : runtime_context["metadata"],
                                "steps"      : runtime_context["steps"],
                                "current"    : runtime_context["current"]
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
                result = await session.call_tool(name, arguments)
                ok = not result.isError

                enhancer: Enhancer = Enhancer(session, mode, model_api, kwargs.get("metadata"))
                fields = await enhancer.enhance(name, arguments, result, ok, stream_logger)

                step_context["ok"] = ok
                step_context["text"] = fields.get("text") if isinstance(fields, dict) else ""
                step_context["data"] = fields.get("data") if isinstance(fields, dict) else None
                step_context["cost_ms"] = int((time.time() - started_at) * 1000)

                runtime_context["steps"].append(step_context)
                runtime_context["current"] = step_context

                emit_event({
                    "type"    : "exec.tool.output",
                    "call_id" : call_id,
                    "name"    : name,
                    "ok"      : ok,
                    "result"  : fields,
                    "cost_ms" : int((time.time() - started_at) * 1000),
                    "ts"      : time.time()
                })

                data = fields.get("data") if isinstance(fields, dict) else None
                data_ok = bool(data.get("ok")) if isinstance(data, dict) else False
                if not ok or not data_ok:
                    step_context["data_ok"] = data_ok
                    brief_err = fields.get("text") if isinstance(fields, dict) else "step failed"
                    await finish(
                        "failed",
                        emit_exec_terminal=execution_started,
                        run=index,
                        index=step_idx,
                        name=name,
                        error=brief_err
                    )
                    return logger.error(f"{fields}\n")

                logger.info(fields.get("text") if isinstance(fields, dict) else "")
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

            if index != loop_count:
                mind.task_info.clear()

    if not has_plan_result:
        err = {"type": "error", "error": "plan stream ended without executable plan.result"}
        await finish("failed", emit_exec_terminal=False, error=json.dumps(err, ensure_ascii=False))
        return logger.error(f"{err}\n")

    await finish("completed", emit_exec_terminal=execution_started)
    await stream_logger.stop()


if __name__ == '__main__':
    pass
