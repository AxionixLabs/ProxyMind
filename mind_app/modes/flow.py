# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import time
import typing
import asyncio
from dataclasses import dataclass
from mind_app.mcp.contracts import McpSessionLike
from engine.scaling import (
    PackItem,
    Pack
)
from engine.observability import (
    observe,
    observe_exception
)
from mind_nova.events import EventReport
from mind_nova.modes import RunMode
from mind_nova.requests.reports import open_report_session
from mind_core.permissions import PermissionSettings
from mind_app.frontend import ApplicationView
from mind_app.runtime.execution import (
    AgentContext,
    TurnContext
)
from mind_app.stream_events.failure_display import render_failure_block
from .code_sources import (
    CodeSourceResolved,
    resolve_code_sources
)
from .result import RunResult
from ..runtime.support.calling import resolve_mode_runner
from mind_nova import const

if typing.TYPE_CHECKING:
    from ..controller import Mind


@dataclass(slots=True)
class FlowConfig:
    """星图编排配置：收敛执行源中的运行参数。"""
    repeat: int
    attempts: int
    pattern: typing.Optional[re.Pattern[str]]
    stop_on_fail: bool
    loop_prefix: str
    loop_suffix: str
    round_prefix: str
    round_suffix: str
    item_prefix: str
    item_suffix: str
    global_prefix: str
    global_suffix: str
    global_rule: str


@dataclass(slots=True)
class FlowRuntime:
    """星图编排运行时：收敛会话、偏好配置和事件上报依赖。"""
    mode: RunMode
    pref_config: dict[str, typing.Any]
    event_report: EventReport
    runner: typing.Callable[..., typing.Awaitable[RunResult]]
    metadata: dict[str, str]
    agent: AgentContext
    permissions: PermissionSettings
    cwd: str
    failures: int = 0


@dataclass(slots=True)
class FlowExecutionContext:
    """星图编排执行上下文：收敛源、配置、报告和运行态。"""
    code_sources: list[CodeSourceResolved]
    pref_config: dict[str, typing.Any]
    metadata: dict[str, str]
    report_url: str | None
    event_report: EventReport
    runtime: FlowRuntime


def _build_flow_config(cfg: dict[str, typing.Any]) -> FlowConfig:
    """把星图配置字典标准化为结构化配置。"""
    try:
        repeat = int(cfg.get("repeat") or 1)
    except (TypeError, ValueError):
        repeat = 1
    if repeat < 1:
        repeat = 1

    pattern      = (cfg.get("pattern") or "").strip()
    name_pattern = re.compile(pattern) if pattern else None

    try:
        attempts = int(cfg.get("attempts") or 3)
    except (TypeError, ValueError):
        attempts = 3
    if attempts < 1:
        attempts = 1

    stop_on_fail = str(cfg.get("stop_on_fail") or "").strip().lower() in {
        "1", "true", "yes", "on"
    }

    return FlowConfig(
        repeat=repeat,
        attempts=attempts,
        pattern=name_pattern,
        stop_on_fail=stop_on_fail,
        loop_prefix=(cfg.get("loop_prefix") or "").strip(),
        loop_suffix=(cfg.get("loop_suffix") or "").strip(),
        round_prefix=(cfg.get("round_prefix") or "").strip(),
        round_suffix=(cfg.get("round_suffix") or "").strip(),
        item_prefix=(cfg.get("item_prefix") or "").strip(),
        item_suffix=(cfg.get("item_suffix") or "").strip(),
        global_prefix=(cfg.get("global_prefix") or "").strip(),
        global_suffix=(cfg.get("global_suffix") or "").strip(),
        global_rule=(cfg.get("global_rule") or "").strip()
    )


def _emit_diagnostic(event_report: EventReport, event_type: str, **payload: typing.Any) -> None:
    """发送星图编排诊断事件，统一补齐时间戳。"""
    if isinstance(payload.get("run"), int):
        event_report.set_round(payload["run"])
        payload.setdefault("round", payload["run"])

    event_report.emit({"type": event_type, "ts": time.time(), **payload})


def _build_task_message(item: PackItem, config: FlowConfig) -> str:
    """组装单个任务的最终提示词。"""
    prefix = (item.meta.get("prefix") or config.global_prefix or "").strip()
    suffix = (item.meta.get("suffix") or config.global_suffix or "").strip()
    rule   = (item.meta.get("rule") or config.global_rule or "").strip()

    final_msg = item.message
    if prefix:
        final_msg = f"{prefix}\n{final_msg}"
    if suffix:
        final_msg = f"{final_msg}\n{suffix}"
    if rule:
        final_msg = f"{final_msg}\n\n{rule}"

    return final_msg


def _first_flow_title(code_sources: list[CodeSourceResolved]) -> str:
    """从星图执行源里取第一条真实任务消息作为 history 标题。"""
    for source in code_sources:
        items, _ = Pack.pack_parse(source.content)
        if items:
            return items[0].message

    return code_sources[0].content if code_sources else ""


async def _run_flow_turn(
    runtime: FlowRuntime,
    session: McpSessionLike,
    message: str,
    tools: list[dict[str, typing.Any]],
    **kwargs: typing.Any
) -> RunResult:
    """为编排中的单次模型请求创建独立轮次上下文。"""
    turn_context = TurnContext.create(
        agent=runtime.agent,
        cid=runtime.metadata["cid"],
        sid=runtime.metadata["sid"],
        mode=runtime.mode,
        source="flow",
        pref_config=runtime.pref_config,
        cwd=runtime.cwd,
        permissions=runtime.permissions,
    )

    runner_kwargs = dict(kwargs)
    runner_kwargs.pop("turn_id", None)
    runner_kwargs.pop("turn_context", None)

    return await runtime.runner(
        session,
        runtime.mode,
        runtime.pref_config,
        message,
        tools,
        turn_context=turn_context,
        **runner_kwargs,
    )


async def _run_virtual_message(
    mind: "Mind",
    runtime: FlowRuntime,
    source: CodeSourceResolved,
    item_count: int,
    session: McpSessionLike,
    tools: list[dict[str, typing.Any]],
    *,
    name: str,
    msg: str,
    run: typing.Optional[int] = None,
    **kwargs
) -> None:
    """执行虚拟消息 hook，用于 loop/round/item 的前后缀扩展。"""
    if not msg.strip():
        return None

    observe(
        "flow.virtual.start",
        name=name,
        source=source.name,
        source_kind=source.kind,
    )
    _emit_diagnostic(
        runtime.event_report,
        event_type="virtual.start",
        file=source.display_origin,
        name=name,
        run=run
    )

    await mind.start_anim(runtime.mode)

    failure_error: typing.Optional[str] = None

    try:
        result = await _run_flow_turn(runtime, session, msg, tools, **kwargs)
        if not result.ok:
            failure_error = result.error or f"run {result.status}"
            _emit_diagnostic(
                runtime.event_report,
                event_type="virtual.failed",
                file=source.display_origin,
                source=source.display_origin,
                total=item_count,
                error=failure_error,
                name=name,
                run=run,
            )
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except BaseException as exc:
        failure_error = Pack.brief_err(exc)
        _emit_diagnostic(
            runtime.event_report,
            event_type="virtual.failed",
            file=source.display_origin,
            source=source.display_origin,
            total=item_count,
            error=failure_error,
            name=name,
            run=run
        )

    finally:
        await mind.await_cleanup(mind.stop_anim("wait"))

    if failure_error:
        runtime.failures += 1
        observe(
            "flow.virtual.failed",
            level="ERROR",
            name=name,
            source=source.name,
            error=failure_error,
        )

    _emit_diagnostic(
        runtime.event_report,
        event_type="virtual.done",
        file=source.display_origin,
        name=name,
        run=run
    )


async def _run_flow_item(
    mind: "Mind",
    runtime: FlowRuntime,
    config: FlowConfig,
    source: CodeSourceResolved,
    item: PackItem,
    *,
    run: int,
    index: int,
    total: int,
    session: McpSessionLike,
    tools: list[dict[str, typing.Any]],
    **kwargs,
) -> bool:
    """执行单个 item，内部负责重试、退避和失败收口。"""
    for item_run in range(1, item.loop + 1):
        _emit_diagnostic(
            runtime.event_report,
            event_type="task.start",
            file=source.display_origin,
            run=run,
            index=index,
            total=total,
            name=item.name,
            item_run=item_run,
            item_total=item.loop
        )

        observe(
            "flow.item.start",
            item=item.name,
            index=index,
            total=total,
            item_run=item_run,
            item_total=item.loop,
            source=source.name,
        )

        last_error: typing.Optional[str] = None

        for attempt in range(1, config.attempts + 1):
            started_at = time.time()

            attempt_error: typing.Optional[str]   = None
            retry_backoff: typing.Optional[float] = None

            _emit_diagnostic(
                runtime.event_report,
                event_type="task.attempt",
                file=source.display_origin,
                run=run,
                index=index,
                total=total,
                name=item.name,
                item_run=item_run,
                item_total=item.loop,
                attempt=attempt,
                max_attempts=config.attempts
            )

            final_msg = _build_task_message(item, config)

            await mind.start_anim(runtime.mode)

            try:
                result = await _run_flow_turn(
                    runtime,
                    session,
                    final_msg,
                    tools,
                    **kwargs,
                )
                if not result.ok:
                    raise RuntimeError(result.error or f"run {result.status}")

                _emit_diagnostic(
                    runtime.event_report,
                    event_type="task.done",
                    file=source.display_origin,
                    run=run,
                    index=index,
                    total=total,
                    name=item.name,
                    item_run=item_run,
                    item_total=item.loop,
                    attempt=attempt,
                    cost_ms=int((time.time() - started_at) * 1000)
                )
                break
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except BaseException as exc:
                attempt_error = Pack.brief_err(exc)
                last_error = attempt_error

                _emit_diagnostic(
                    runtime.event_report,
                    event_type="task.failed",
                    file=source.display_origin,
                    run=run,
                    index=index,
                    total=total,
                    name=item.name,
                    item_run=item_run,
                    item_total=item.loop,
                    attempt=attempt,
                    max_attempts=config.attempts,
                    error=attempt_error
                )

                if attempt < config.attempts:
                    retry_backoff = 0.5 * (2 ** (attempt - 1))
                    _emit_diagnostic(
                        runtime.event_report,
                        event_type="task.retry_wait",
                        file=source.display_origin,
                        run=run,
                        index=index,
                        total=total,
                        name=item.name,
                        item_run=item_run,
                        item_total=item.loop,
                        attempt=attempt,
                        wait_s=retry_backoff
                    )
            finally:
                await mind.await_cleanup(mind.stop_anim("wait"))

            if attempt_error:
                observe(
                    "flow.item.attempt_failed",
                    level="WARNING",
                    item=item.name,
                    item_run=item_run,
                    item_total=item.loop,
                    attempt=attempt,
                    max_attempts=config.attempts,
                    error=attempt_error,
                )

            if retry_backoff is not None:
                await asyncio.sleep(retry_backoff)

        else:
            _emit_diagnostic(
                runtime.event_report,
                event_type="task.give_up",
                file=source.display_origin,
                run=run,
                index=index,
                total=total,
                name=item.name,
                item_run=item_run,
                item_total=item.loop,
                max_attempts=config.attempts,
                error=last_error
            )

            observe(
                "flow.item.failed",
                level="ERROR",
                item=item.name,
                item_run=item_run,
                item_total=item.loop,
                attempts=config.attempts,
                error=last_error,
            )
            runtime.failures += 1
            if config.stop_on_fail:
                return False

    return True


async def _run_flow_source(
    mind: "Mind",
    runtime: FlowRuntime,
    source: CodeSourceResolved,
    session: McpSessionLike,
    tools: list[dict[str, typing.Any]],
    **kwargs
) -> None:
    """执行单个星图源，负责 round、item 和 hook 的整体编排。"""
    items, raw_cfg = Pack.pack_parse(source.content)

    config = _build_flow_config(raw_cfg)

    if not items:
        observe(
            "flow.source.empty",
            level="WARNING",
            source=source.name,
            source_kind=source.kind,
        )

    item_total = len(items)

    _emit_diagnostic(
        runtime.event_report,
        event_type="flow.start",
        file=source.display_origin,
        items=item_total,
        repeat=config.repeat
    )

    await _run_virtual_message(
        mind,
        runtime,
        source,
        item_total,
        session,
        tools,
        name="__loop_prefix__",
        msg=config.loop_prefix,
        **kwargs
    )

    try:
        for run in range(1, config.repeat + 1):
            _emit_diagnostic(
                runtime.event_report,
                event_type="round.start",
                file=source.display_origin,
                run=run,
                total=config.repeat,
                items=item_total
            )

            await _run_virtual_message(
                mind,
                runtime,
                source,
                item_total,
                session,
                tools,
                name="__round_prefix__",
                msg=config.round_prefix,
                run=run,
                **kwargs
            )

            observe(
                "flow.run.start",
                run=run,
                total_runs=config.repeat,
                items=item_total,
                source=source.name,
            )

            for index, item in enumerate(items, start=1):
                if config.pattern and not config.pattern.search(item.name):
                    _emit_diagnostic(
                        runtime.event_report,
                        event_type="task.skip",
                        file=source.display_origin,
                        run=run,
                        index=index,
                        total=item_total,
                        name=item.name,
                        item_total=item.loop,
                        reason="filter"
                    )
                    observe(
                        "flow.item.skipped",
                        item=item.name,
                        index=index,
                        total=item_total,
                        reason="filter",
                    )
                    continue

                # 每个 item 在真实任务前后都保留 hook，方便统一注入上下文。
                _emit_diagnostic(
                        runtime.event_report,
                        event_type="item_hook.start",
                        hook="item_prefix",
                        file=source.display_origin,
                    run=run,
                    index=index,
                    total=item_total,
                    name=item.name
                )
                await _run_virtual_message(
                    mind,
                    runtime,
                    source,
                    item_total,
                    session,
                    tools,
                    name="__item_prefix__",
                    msg=config.item_prefix,
                    run=run,
                    **kwargs
                )
                _emit_diagnostic(
                        runtime.event_report,
                        event_type="item_hook.done",
                        hook="item_prefix",
                        file=source.display_origin,
                    run=run,
                    index=index,
                    total=item_total,
                    name=item.name
                )

                try:
                    should_continue = await _run_flow_item(
                        mind,
                        runtime,
                        config,
                        source,
                        item,
                        run=run,
                        index=index,
                        total=item_total,
                        session=session,
                        tools=tools,
                        **kwargs
                    )
                    if not should_continue:
                        return None

                finally:
                    _emit_diagnostic(
                        runtime.event_report,
                        event_type="item_hook.start",
                        hook="item_suffix",
                        file=source.display_origin,
                        run=run,
                        index=index,
                        total=item_total,
                        name=item.name
                    )
                    await _run_virtual_message(
                        mind,
                        runtime,
                        source,
                        item_total,
                        session,
                        tools,
                        name="__item_suffix__",
                        msg=config.item_suffix,
                        run=run,
                        **kwargs
                    )
                    _emit_diagnostic(
                        runtime.event_report,
                        event_type="item_hook.done",
                        hook="item_suffix",
                        file=source.display_origin,
                        run=run,
                        index=index,
                        total=item_total,
                        name=item.name
                    )

            await _run_virtual_message(
                mind,
                runtime,
                source,
                item_total,
                session,
                tools,
                name="__round_suffix__",
                msg=config.round_suffix,
                run=run,
                **kwargs
            )
            _emit_diagnostic(
                runtime.event_report,
                event_type="round.done",
                file=source.display_origin,
                run=run,
                total=config.repeat,
                items=item_total
            )

        await _run_virtual_message(
            mind,
            runtime,
            source,
            item_total,
            session,
            tools,
            name="__loop_suffix__",
            msg=config.loop_suffix,
            **kwargs
        )
    finally:
        _emit_diagnostic(
            runtime.event_report,
            event_type="flow.done",
            file=source.display_origin,
            items=item_total,
            repeat=config.repeat
        )


async def _open_flow_report_url(
    mode: RunMode,
    metadata: dict[str, str]
) -> str | None:
    """打开星图编排报告会话并返回可访问地址。"""
    try:
        report_data = await open_report_session(
            mode,
            metadata["cid"],
            metadata["sid"],
            proto=f"{const.APP_NAME}.flow"
        )
        report_url_raw = report_data.get("report_url")
        report_url     = report_url_raw.strip() if isinstance(report_url_raw, str) else None
        report_id      = str(report_data.get("report_id") or "").strip()

        if not report_url:
            observe(
                "flow.report.missing_url",
                level="WARNING",
                cid=metadata["cid"],
                sid=metadata["sid"],
                report_id=report_id,
            )
        return report_url
    except Exception as exc:
        observe_exception(
            "flow.report.failed",
            exc,
            level="WARNING",
            cid=metadata["cid"],
            sid=metadata["sid"],
        )
        return None


async def _prepare_flow_context(
    mind: "Mind",
    code: list[typing.Any],
    mode: RunMode,
    kwargs: dict[str, typing.Any]
) -> FlowExecutionContext:
    """准备星图编排执行所需上下文。"""
    code_sources = await resolve_code_sources(code)
    pref_config  = await mind.fresh_pref_config(ttl_sec=0.0)
    runner       = resolve_mode_runner(mind, mode)

    meta_in = kwargs.get("metadata") if isinstance(kwargs.get("metadata"), dict) else {}
    cid     = meta_in.get("cid") if isinstance(meta_in, dict) else None
    sid     = meta_in.get("sid") if isinstance(meta_in, dict) else None

    first_title = _first_flow_title(code_sources)

    metadata = {
        **meta_in,
        **mind.begin_conversation_turn(
            cid=cid,
            sid=sid,
            title=first_title,
            source="flow",
        )
    }
    kwargs["metadata"] = metadata

    permissions = kwargs.get("permissions") or mind.permissions
    kwargs["permissions"] = permissions

    report_url = await _open_flow_report_url(mode, metadata)

    event_report = EventReport(mode, metadata["cid"], metadata["sid"], proto=f"{const.APP_NAME}.flow")

    kwargs["ev_report"] = event_report
    await event_report.open()

    event_report.begin_turn(round_no=1)

    runtime = FlowRuntime(
        mode=mode,
        pref_config=pref_config,
        event_report=event_report,
        runner=runner,
        metadata=metadata,
        agent=AgentContext.root(metadata["sid"]),
        permissions=permissions,
        cwd=mind.history_workspace,
    )
    return FlowExecutionContext(
        code_sources=code_sources,
        pref_config=pref_config,
        metadata=metadata,
        report_url=report_url,
        event_report=event_report,
        runtime=runtime
    )


async def _run_flow_sources(
    mind: "Mind",
    context: FlowExecutionContext,
    session: McpSessionLike,
    tools: list[dict[str, typing.Any]],
    kwargs: dict[str, typing.Any]
) -> None:
    """顺序执行星图源。"""
    for source in context.code_sources:
        await _run_flow_source(
            mind, context.runtime, source, session, tools, **kwargs
        )


async def _close_flow_report(
    mind: "Mind",
    event_report: EventReport
) -> None:
    """关闭星图编排事件报告。"""
    await mind.await_cleanup(event_report.flush())
    await mind.await_cleanup(event_report.close())


async def run_flow(
    mind: "Mind",
    code: list[typing.Any],
    mode: RunMode,
    *_,
    **kwargs
) -> RunResult:
    """星图编排入口：绑定会话、事件流和执行源。"""
    started_at = time.perf_counter()

    observe("flow.start", mode=mode, requested_sources=len(code))

    try:
        context = await _prepare_flow_context(mind, code, mode, kwargs)
    except (asyncio.CancelledError, KeyboardInterrupt) as error:
        observe_exception(
            "flow.interrupted",
            error,
            level="WARNING",
            mode=mode,
            phase="prepare",
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
        )
        raise
    except BaseException as error:
        observe_exception(
            "flow.failed",
            error,
            mode=mode,
            phase="prepare",
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
        )
        raise

    observe(
        "flow.prepared",
        mode=mode,
        sources=len(context.code_sources),
        cid=context.metadata.get("cid"),
        sid=context.metadata.get("sid"),
    )

    def before_user_flow() -> None:
        if context.report_url:
            observe("flow.report.available")

    async def function(
        session: McpSessionLike,
        tools: list[dict[str, typing.Any]],
    ) -> None:
        """在共享 MCP 会话中顺序执行多个星图源。"""
        await _run_flow_sources(mind, context, session, tools, kwargs)

    try:
        await mind.with_mcp_session(context.pref_config, function, before_user_flow=before_user_flow)

    except (asyncio.CancelledError, KeyboardInterrupt) as error:
        observe_exception(
            "flow.interrupted",
            error,
            level="WARNING",
            mode=mode,
            phase="execute",
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
        )
        raise

    except BaseException as exc:
        error = Pack.brief_err(exc)
        observe_exception(
            "flow.failed",
            exc,
            mode=mode,
            phase="execute",
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
        )

        _emit_diagnostic(
            context.event_report,
            event_type="flow.failed",
            error=error
        )

        mind.frontend.application.emit(ApplicationView(
            type="flow.failed",
            renderable=render_failure_block(
                "flow.failed",
                error,
                terminal_width=mind.frontend.application.viewport.width,
            ),
        ))
        mind.frontend.application.emit(ApplicationView(type="run.gap"))

        run_result = RunResult(
            status="failed",
            assistant_text=mind.last_assistant_reply_snapshot(),
            error=error,
        )

    else:
        if context.runtime.failures:
            observe(
                "flow.failed",
                level="ERROR",
                mode=mode,
                sources=len(context.code_sources),
                failures=context.runtime.failures,
                elapsed_ms=int((time.perf_counter() - started_at) * 1000),
            )
            run_result = RunResult(
                status="failed",
                assistant_text=mind.last_assistant_reply_snapshot(),
                error=f"{context.runtime.failures} flow step(s) failed",
            )
        else:
            observe(
                "flow.complete",
                mode=mode,
                sources=len(context.code_sources),
                elapsed_ms=int((time.perf_counter() - started_at) * 1000),
            )
            run_result = RunResult(
                status="completed",
                assistant_text=mind.last_assistant_reply_snapshot(),
            )

    finally:
        await _close_flow_report(mind, context.event_report)

    return run_result


if __name__ == '__main__':
    pass
