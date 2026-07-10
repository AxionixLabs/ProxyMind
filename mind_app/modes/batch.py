# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import time
import typing
import asyncio
from dataclasses import dataclass
from loguru import logger
from mind_app.mcp import McpSessionLike
from engine.scaling import (
    PackItem, Pack
)
from mind_nova.events import EventReport
from mind_nova.modes import RunMode
from mind_nova.request import open_report_session
from mind_app.stream_events.failure_display import render_failure_text
from mind_core.design import Design
from .code_sources import (
    CodeSourceResolved, resolve_code_sources
)
from ..runtime.support.calling import resolve_mode_runner
from mind_nova import const

if typing.TYPE_CHECKING:
    from ..mind_core import Mind


@dataclass(slots=True)
class PackConfig:
    """批处理配置：收敛 pack 文件中的运行参数。"""
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
class PackRuntime:
    """批处理运行时：收敛会话、偏好配置和事件上报依赖。"""
    mode: RunMode
    pref_config: dict[str, typing.Any]
    event_report: EventReport
    runner: typing.Callable[..., typing.Awaitable[None]]


@dataclass(slots=True)
class PackExecutionContext:
    """批处理执行上下文：收敛源、配置、报告和运行态。"""
    code_sources: list[CodeSourceResolved]
    pref_config: dict[str, typing.Any]
    metadata: dict[str, str]
    report_url: str | None
    event_report: EventReport
    runtime: PackRuntime


def _build_pack_config(cfg: dict[str, typing.Any]) -> PackConfig:
    """把 pack 配置字典标准化为结构化配置。"""
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

    return PackConfig(
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
    """发送批处理诊断事件，统一补齐时间戳。"""
    if isinstance(payload.get("run"), int):
        event_report.set_round(payload["run"])
        payload.setdefault("round", payload["run"])

    event_report.emit({"type": event_type, "ts": time.time(), **payload})


def _build_task_message(item: PackItem, config: PackConfig) -> str:
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


def _first_pack_title(code_sources: list[CodeSourceResolved]) -> str:
    """从批处理源里取第一条真实任务消息作为 history 标题。"""
    for source in code_sources:
        items, _ = Pack.pack_parse(source.content)
        if items:
            return items[0].message

    return code_sources[0].content if code_sources else ""


async def _run_virtual_message(
    mind: "Mind",
    runtime: PackRuntime,
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

    logger.info(
        f"🧩 [Batch] {name} source={source.display_origin}"
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
        await runtime.runner(
            session,
            runtime.mode,
            runtime.pref_config,
            msg,
            tools,
            **kwargs
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
        await mind.await_cleanup(mind.stop_anim())

    if failure_error:
        logger.error(
            f"❌ [Batch] virtual failed: {name} source={source.display_origin} err={failure_error}\n"
        )

    _emit_diagnostic(
        runtime.event_report,
        event_type="virtual.done",
        file=source.display_origin,
        name=name,
        run=run
    )


async def _run_pack_item(
    mind: "Mind",
    runtime: PackRuntime,
    config: PackConfig,
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

        logger.info(
            f"▶️  [Batch] [{index}/{total}] {item.name} "
            f"item_run={item_run}/{item.loop} source={source.display_origin}"
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
                await runtime.runner(
                    session,
                    runtime.mode,
                    runtime.pref_config,
                    final_msg,
                    tools,
                    **kwargs
                )

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
                await mind.await_cleanup(mind.stop_anim())

            if attempt_error:
                logger.error(
                    f"❌ [Batch] item failed: {item.name} "
                    f"item_run={item_run}/{item.loop} "
                    f"attempt={attempt}/{config.attempts} err={attempt_error}\n"
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

            logger.error(
                f"🧯 [Batch] giving up: {item.name} "
                f"item_run={item_run}/{item.loop} "
                f"attempts={config.attempts} last={last_error}"
            )
            if config.stop_on_fail:
                return False

    return True


async def _run_pack_source(
    mind: "Mind",
    runtime: PackRuntime,
    source: CodeSourceResolved,
    session: McpSessionLike,
    tools: list[dict[str, typing.Any]],
    **kwargs
) -> None:
    """执行单个 pack 源，负责 round/item/hook 的整体编排。"""
    items, raw_cfg = Pack.pack_parse(source.content)

    config = _build_pack_config(raw_cfg)

    if not items:
        logger.warning(
            f"[Batch] no executable items in pack; running hooks only. source={source.display_origin}"
        )

    item_total = len(items)

    _emit_diagnostic(
        runtime.event_report,
        event_type="batch.start",
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

            logger.info(
                f"🧪 [Batch] run {run}/{config.repeat} items={item_total} source={source.display_origin}"
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
                    logger.debug(
                        f"⏭️  [Batch] skip [{index}/{item_total}] {item.name} (filter)"
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
                    should_continue = await _run_pack_item(
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
            event_type="batch.done",
            file=source.display_origin,
            items=item_total,
            repeat=config.repeat
        )


async def _open_pack_report_url(
    mode: RunMode,
    metadata: dict[str, str]
) -> str | None:
    """打开批处理报告会话并返回可访问地址。"""
    try:
        report_data = await open_report_session(
            mode,
            metadata["cid"],
            metadata["sid"],
            proto=f"{const.APP_NAME}.batch"
        )
        report_url_raw = report_data.get("report_url")
        report_url     = report_url_raw.strip() if isinstance(report_url_raw, str) else None
        report_id      = str(report_data.get("report_id") or "").strip()

        if not report_url:
            logger.warning(
                "[Batch] reports/open succeeded but report_url missing "
                f"cid={metadata['cid']} sid={metadata['sid']} report_id={report_id or '-'}"
            )
        return report_url
    except Exception as exc:
        logger.warning(
            "[Batch] reports/open failed "
            f"cid={metadata['cid']} sid={metadata['sid']} "
            f"error_type={type(exc).__name__} error={exc}"
        )
        return None


async def _prepare_pack_context(
    mind: "Mind",
    code: list[typing.Any],
    mode: RunMode,
    kwargs: dict[str, typing.Any]
) -> PackExecutionContext:
    """准备批处理执行所需上下文。"""
    code_sources = await resolve_code_sources(code)
    pref_config  = await mind.fresh_pref_config(ttl_sec=0.0)
    runner       = resolve_mode_runner(mind, mode)

    meta_in = kwargs.get("metadata") if isinstance(kwargs.get("metadata"), dict) else {}
    cid     = meta_in.get("cid") if isinstance(meta_in, dict) else None
    sid     = meta_in.get("sid") if isinstance(meta_in, dict) else None

    first_title = _first_pack_title(code_sources)

    metadata = {
        **meta_in,
        **mind.begin_session(cid=cid, sid=sid, mode=mode, title=first_title, source="batch")
    }
    kwargs["metadata"] = metadata

    report_url = await _open_pack_report_url(mode, metadata)

    event_report = EventReport(mode, metadata["cid"], metadata["sid"], proto=f"{const.APP_NAME}.batch")

    kwargs["ev_report"] = event_report
    await event_report.open()

    event_report.begin_turn(round_no=1)

    runtime = PackRuntime(
        mode=mode,
        pref_config=pref_config,
        event_report=event_report,
        runner=runner
    )
    return PackExecutionContext(
        code_sources=code_sources,
        pref_config=pref_config,
        metadata=metadata,
        report_url=report_url,
        event_report=event_report,
        runtime=runtime
    )


async def _run_pack_sources(
    mind: "Mind",
    context: PackExecutionContext,
    session: McpSessionLike,
    tools: list[dict[str, typing.Any]],
    kwargs: dict[str, typing.Any]
) -> None:
    """顺序执行批处理源。"""
    for source in context.code_sources:
        await _run_pack_source(
            mind, context.runtime, source, session, tools, **kwargs
        )


async def _close_pack_report(
    mind: "Mind",
    event_report: EventReport
) -> None:
    """关闭批处理事件报告。"""
    await mind.await_cleanup(event_report.flush())
    await mind.await_cleanup(event_report.close())


async def mind_pack(
    mind: "Mind",
    code: list[typing.Any],
    mode: RunMode,
    *_,
    **kwargs
) -> None:
    """批处理入口：绑定会话、事件流和 pack 源执行流程。"""
    context = await _prepare_pack_context(mind, code, mode, kwargs)

    def before_user_flow() -> None:
        if context.report_url:
            logger.info(f"🌐 Atlas: {context.report_url}")

    async def function(
        session: McpSessionLike,
        tools: list[dict[str, typing.Any]],
    ) -> None:
        """在共享 MCP 会话中顺序执行多个 pack 文件。"""
        await _run_pack_sources(mind, context, session, tools, kwargs)

    try:
        return await mind.with_mcp_session(context.pref_config, function, before_user_flow=before_user_flow)

    except (asyncio.CancelledError, KeyboardInterrupt):
        raise

    except BaseException as exc:
        error = Pack.brief_err(exc)

        _emit_diagnostic(
            context.event_report,
            event_type="batch.failed",
            error=error
        )

        logger.error(f"❌ [Batch] failed: {error}\n")
        Design.console.print(render_failure_text("batch.failed", error))
        Design.console.print()

        return None

    finally:
        await _close_pack_report(mind, context.event_report)


if __name__ == '__main__':
    pass
