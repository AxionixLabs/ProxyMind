# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import time
import typing
import asyncio
from pathlib import Path
from dataclasses import dataclass
from loguru import logger
from mcp import ClientSession
from engine.scaling import (
    PackItem, Pack
)
from engine.tinker import MindError
from mind_nova.events import EventReport
from mind_nova import const

if typing.TYPE_CHECKING:
    from .mind_core import Mind


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
    """批处理运行时：收敛会话、模型和事件上报依赖。"""

    mode: typing.Literal["chat", "fast", "plan"]
    model_api: dict[str, typing.Any]
    event_report: EventReport
    runner: typing.Callable[..., typing.Awaitable[None]]


def _resolve_code_paths(code: list[str]) -> list[Path]:
    """解析批处理输入文件，并校验文件是否存在。"""

    code_path = [Path(x).expanduser() for x in (code or [])]

    if not code_path:
        raise MindError("Code list is empty")

    for code_p in code_path:
        if not code_p.exists():
            raise MindError(f"File not found: {code_p}")

    return code_path


def _build_pack_config(cfg: dict[str, typing.Any]) -> PackConfig:
    """把 pack 配置字典标准化为结构化配置。"""

    try:
        repeat = int(cfg.get("repeat") or 1)
    except (TypeError, ValueError):
        repeat = 1
    if repeat < 1:
        repeat = 1

    pattern = (cfg.get("pattern") or "").strip()
    name_pattern = re.compile(pattern) if pattern else None

    try:
        attempts = int(cfg.get("attempts") or 3)
    except (TypeError, ValueError):
        attempts = 3
    if attempts < 1:
        attempts = 1

    stop_on_fail = str(cfg.get("stop_on_fail") or "").strip().lower() in {
        "1", "true", "yes", "on",
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

    rule = (item.meta.get("rule") or config.global_rule or "").strip()

    final_msg = item.message
    if prefix:
        final_msg = f"{prefix}\n{final_msg}"
    if suffix:
        final_msg = f"{final_msg}\n{suffix}"
    if rule:
        final_msg = f"{final_msg}\n\n{rule}"

    return final_msg


async def _run_virtual_message(
    mind: "Mind",
    runtime: PackRuntime,
    file_path: Path,
    item_count: int,
    session: ClientSession,
    openai_tools: list[dict[str, typing.Any]],
    tool_meta: dict[str, dict[str, typing.Any]],
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
        f"🧩 [Batch] {name} file={file_path}"
    )
    _emit_diagnostic(
        runtime.event_report, event_type="virtual.start", file=str(file_path), name=name, run=run
    )

    await mind.start_anim(runtime.mode)

    try:
        await runtime.runner(
            session,
            runtime.mode,
            runtime.model_api,
            msg,
            openai_tools,
            tool_meta,
            **kwargs
        )
    except BaseException as exc:
        error = Pack.brief_err(exc)
        _emit_diagnostic(
            runtime.event_report,
            event_type="virtual.failed",
            file=str(file_path),
            total=item_count,
            error=error,
            name=name,
            run=run
        )
        logger.error(
            f"❌ [Batch] virtual failed: {name} file={file_path} err={error}\n"
        )

    finally:
        await mind.stop_anim()

    _emit_diagnostic(
        runtime.event_report, event_type="virtual.done", file=str(file_path), name=name, run=run
    )


async def _run_pack_item(
    mind: "Mind",
    runtime: PackRuntime,
    config: PackConfig,
    file_path: Path,
    item: PackItem,
    *,
    run: int,
    index: int,
    total: int,
    session: ClientSession,
    openai_tools: list[dict[str, typing.Any]],
    tool_meta: dict[str, dict[str, typing.Any]],
    **kwargs,
) -> bool:
    """执行单个 item，内部负责重试、退避和失败收口。"""

    for item_run in range(1, item.loop + 1):
        _emit_diagnostic(
            runtime.event_report,
            event_type="task.start",
            file=str(file_path),
            run=run,
            index=index,
            total=total,
            name=item.name,
            item_run=item_run,
            item_total=item.loop
        )

        logger.info(
            f"▶️  [Batch] [{index}/{total}] {item.name} "
            f"item_run={item_run}/{item.loop} file={file_path}"
        )

        last_error: typing.Optional[str] = None

        for attempt in range(1, config.attempts + 1):
            started_at = time.time()

            _emit_diagnostic(
                runtime.event_report,
                event_type="task.attempt",
                file=str(file_path),
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
                    runtime.model_api,
                    final_msg,
                    openai_tools,
                    tool_meta,
                    **kwargs
                )

                _emit_diagnostic(
                    runtime.event_report,
                    event_type="task.done",
                    file=str(file_path),
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

            except BaseException as exc:
                error = Pack.brief_err(exc)
                last_error = error

                _emit_diagnostic(
                    runtime.event_report,
                    event_type="task.failed",
                    file=str(file_path),
                    run=run,
                    index=index,
                    total=total,
                    name=item.name,
                    item_run=item_run,
                    item_total=item.loop,
                    attempt=attempt,
                    max_attempts=config.attempts,
                    error=error
                )

                logger.error(
                    f"❌ [Batch] item failed: {item.name} "
                    f"item_run={item_run}/{item.loop} "
                    f"attempt={attempt}/{config.attempts} err={error}\n"
                )

                if attempt < config.attempts:
                    backoff = 0.5 * (2 ** (attempt - 1))
                    _emit_diagnostic(
                        runtime.event_report,
                        event_type="task.retry_wait",
                        file=str(file_path),
                        run=run,
                        index=index,
                        total=total,
                        name=item.name,
                        item_run=item_run,
                        item_total=item.loop,
                        attempt=attempt,
                        wait_s=backoff
                    )
                    await asyncio.sleep(backoff)
            finally:
                await mind.stop_anim()

        else:
            _emit_diagnostic(
                runtime.event_report,
                event_type="task.give_up",
                file=str(file_path),
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


async def _run_pack_file(
    mind: "Mind",
    runtime: PackRuntime,
    file_path: Path,
    session: ClientSession,
    openai_tools: list[dict[str, typing.Any]],
    tool_meta: dict[str, dict[str, typing.Any]],
    **kwargs
) -> None:
    """执行单个 pack 文件，负责 round/item/hook 的整体编排。"""

    try:
        text = file_path.read_text(encoding=const.CHARSET, errors="replace")
    except Exception as e:
        raise MindError(e)

    items, raw_cfg = Pack.pack_parse(text)

    config = _build_pack_config(raw_cfg)

    if not items:
        logger.warning(
            f"[Batch] no executable items in pack; running hooks only. file={file_path}"
        )

    item_total = len(items)

    _emit_diagnostic(
        runtime.event_report, event_type="batch.start", file=str(file_path), items=item_total, repeat=config.repeat
    )

    await _run_virtual_message(
        mind,
        runtime,
        file_path,
        item_total,
        session,
        openai_tools,
        tool_meta,
        name="__loop_prefix__",
        msg=config.loop_prefix,
        **kwargs
    )

    try:
        for run in range(1, config.repeat + 1):
            _emit_diagnostic(
                runtime.event_report,
                event_type="round.start",
                file=str(file_path),
                run=run,
                total=config.repeat,
                items=item_total
            )

            await _run_virtual_message(
                mind,
                runtime,
                file_path,
                item_total,
                session,
                openai_tools,
                tool_meta,
                name="__round_prefix__",
                msg=config.round_prefix,
                run=run,
                **kwargs
            )

            logger.info(
                f"🧪 [Batch] run {run}/{config.repeat} items={item_total} file={file_path}"
            )

            for index, item in enumerate(items, start=1):
                if config.pattern and not config.pattern.search(item.name):
                    _emit_diagnostic(
                        runtime.event_report,
                        event_type="task.skip",
                        file=str(file_path),
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
                    file=str(file_path),
                    run=run,
                    index=index,
                    total=item_total,
                    name=item.name
                )
                await _run_virtual_message(
                    mind,
                    runtime,
                    file_path,
                    item_total,
                    session,
                    openai_tools,
                    tool_meta,
                    name="__item_prefix__",
                    msg=config.item_prefix,
                    run=run,
                    **kwargs
                )
                _emit_diagnostic(
                    runtime.event_report,
                    event_type="item_hook.done",
                    hook="item_prefix",
                    file=str(file_path),
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
                        file_path,
                        item,
                        run=run,
                        index=index,
                        total=item_total,
                        session=session,
                        openai_tools=openai_tools,
                        tool_meta=tool_meta,
                        **kwargs
                    )
                    if not should_continue:
                        return None

                finally:
                    _emit_diagnostic(
                        runtime.event_report,
                        event_type="item_hook.start",
                        hook="item_suffix",
                        file=str(file_path),
                        run=run,
                        index=index,
                        total=item_total,
                        name=item.name
                    )
                    await _run_virtual_message(
                        mind,
                        runtime,
                        file_path,
                        item_total,
                        session,
                        openai_tools,
                        tool_meta,
                        name="__item_suffix__",
                        msg=config.item_suffix,
                        run=run,
                        **kwargs
                    )
                    _emit_diagnostic(
                        runtime.event_report,
                        event_type="item_hook.done",
                        hook="item_suffix",
                        file=str(file_path),
                        run=run,
                        index=index,
                        total=item_total,
                        name=item.name
                    )

            await _run_virtual_message(
                mind,
                runtime,
                file_path,
                item_total,
                session,
                openai_tools,
                tool_meta,
                name="__round_suffix__",
                msg=config.round_suffix,
                run=run,
                **kwargs
            )
            _emit_diagnostic(
                runtime.event_report,
                event_type="round.done",
                file=str(file_path),
                run=run,
                total=config.repeat,
                items=item_total
            )

        await _run_virtual_message(
            mind,
            runtime,
            file_path,
            item_total,
            session,
            openai_tools,
            tool_meta,
            name="__loop_suffix__",
            msg=config.loop_suffix,
            **kwargs
        )
    finally:
        _emit_diagnostic(
            runtime.event_report, event_type="batch.done", file=str(file_path), items=item_total, repeat=config.repeat
        )


async def mind_pack(
    mind: "Mind",
    code: list[str],
    mode: typing.Literal["chat", "fast", "plan"],
    runner: typing.Callable[..., typing.Awaitable[None]],
    *_,
    **kwargs
) -> None:
    """批处理入口：绑定会话、事件流和 pack 文件执行流程。"""

    code_path = _resolve_code_paths(code)
    model_api = mind.pref.to_config()

    meta_in = kwargs.get("metadata") or {}
    cid = meta_in.get("cid") if isinstance(meta_in, dict) else None
    sid = meta_in.get("sid") if isinstance(meta_in, dict) else None
    kwargs["metadata"] = meta = mind.begin_session(cid=cid, sid=sid)

    atlas = f"{const.ATLAS_URL}?mode={mode}&cid={meta['cid']}&sid={meta['sid']}"
    logger.info(f"🌐 Atlas: {atlas}")

    event_report = EventReport(mode, meta["cid"], meta["sid"], proto="mind.batch")
    kwargs["ev_report"] = event_report
    await event_report.open()
    event_report.begin_turn(round_no=1)

    runtime = PackRuntime(mode=mode, model_api=model_api, event_report=event_report, runner=runner)

    async def function(
        session: ClientSession,
        openai_tools: list[dict[str, typing.Any]],
        tool_meta: dict[str, dict[str, typing.Any]]
    ) -> None:
        """在共享 MCP 会话中顺序执行多个 pack 文件。"""

        try:
            for path in code_path:
                await _run_pack_file(
                    mind, runtime, path, session, openai_tools, tool_meta, **kwargs
                )
        finally:
            await event_report.flush()
            await event_report.close()

    return await mind.with_mcp_session(model_api, function)


if __name__ == '__main__':
    pass
