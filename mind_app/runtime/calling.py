# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import httpx
import typing
from loguru import logger
from mcp import ClientSession
from mind_nova.events import EventReport
from mind_nova import const
from ..stream_ui import StreamUI

if typing.TYPE_CHECKING:
    from ..mind_core import Mind


def resolve_mode_runner(
    mind: "Mind",
    mode: typing.Literal["chat", "fast", "plan"],
) -> typing.Callable[..., typing.Awaitable[None]]:
    """根据单次调用模式选择底层执行器。"""
    if mode in {"chat", "fast"}:
        return mind.stream_looper
    if mode == "plan":
        return mind.static_looper
    raise ValueError(f"Unsupported mode: {mode}")


def _flatten_exceptions(exc: BaseException) -> typing.Generator[BaseException, None, None]:
    """展开异常组，便于统一记录底层异常。"""
    if isinstance(exc, BaseExceptionGroup):
        for sub in exc.exceptions:
            yield from _flatten_exceptions(sub)
    else:
        yield exc


async def with_mcp_guard(
    mind: "Mind",
    runner: typing.Callable[..., typing.Awaitable[None]],
    *,
    mode: typing.Literal["chat", "fast", "plan"] = "chat",
    **kwargs
) -> None:
    """为模式执行增加动画、网络异常和 HTTP 异常保护层。"""
    network_errors: list[BaseException] = []
    http_errors: list[BaseException] = []
    runtime_errors: list[BaseException] = []

    await mind.start_anim(mode)

    try:
        await runner(mode=mode, **kwargs)

    except* (httpx.ConnectError, httpx.ProxyError, httpx.TimeoutException) as error_group:
        network_errors.extend(_flatten_exceptions(error_group))

    except* httpx.HTTPStatusError as error_group:
        http_errors.extend(_flatten_exceptions(error_group))

    except* Exception as error_group:
        runtime_errors.extend(_flatten_exceptions(error_group))

    finally:
        await mind.await_cleanup(mind.stop_anim())

    for error_item in network_errors:
        logger.error(f"❌ [Network Error] {error_item!r}\n")

    for error_item in http_errors:
        if isinstance(error_item, httpx.HTTPStatusError):
            body = error_item.response.extensions.get("error_body", b"")
            text = body.decode(const.CHARSET, errors="replace").strip()
            if text:
                logger.error(f"❌ [HTTP Error] {error_item.response.status_code} {text}\n")
            else:
                logger.error(f"❌ [HTTP Error] {error_item.response.status_code}\n")
        else:
            logger.error(f"❌ [HTTP Error] unexpected: {error_item!r}\n")

    for error_item in runtime_errors:
        logger.error(f"❌ [Runtime Error] {error_item!r}\n")


async def wakeup(
    mind: "Mind",
    session: ClientSession,
    stream_ui: typing.Optional[StreamUI] = None
) -> typing.Optional[str]:
    """按 TTL 触发设备刷新，避免高频重复 refresh。"""

    if ((now := time.time()) - mind.last_refresh_ts) < mind.ttl_sec:
        tip = f"ttl-hit: skip refresh ttl={mind.ttl_sec:.3f}s"
        if stream_ui and mind.level != const.SHOW_LEVEL:
            return await stream_ui.feed(tip, display=StreamUI.BLOCK)
        return logger.debug(tip)

    result = await session.call_tool("refresh", {"ttl_sec": mind.ttl_sec})
    ok = not result.isError
    content = result.content[0].text

    if not ok:
        return content

    mind.last_refresh_ts = now

    if stream_ui and mind.level != const.SHOW_LEVEL:
        return await stream_ui.feed(content, display=StreamUI.BLOCK)
    return logger.debug(content)


async def calling(
    mind: "Mind",
    model_api: typing.Optional[dict[str, typing.Any]] = None,
    *,
    message: str,
    mode: typing.Literal["chat", "fast", "plan"] = "chat",
    **kwargs
) -> None:
    """统一包装一次用户调用，并由 mode 决定底层执行器。"""
    model_api = model_api or mind.pref.to_config()

    runner = resolve_mode_runner(mind, mode)

    meta_in = kwargs.get("metadata") if isinstance(kwargs.get("metadata"), dict) else {}
    cid = meta_in.get("cid") if isinstance(meta_in, dict) else None
    sid = meta_in.get("sid") if isinstance(meta_in, dict) else None
    kwargs["metadata"] = meta = {
        **meta_in,
        **mind.begin_session(cid=cid, sid=sid)
    }

    event_report = kwargs.get("ev_report")
    owns_event_report = False
    if not event_report:
        event_report = EventReport(mode, meta["cid"], meta["sid"])
        kwargs["ev_report"] = event_report
        await event_report.open()
        owns_event_report = True

    async def function(
        session: ClientSession,
        openai_tools: list[dict[str, typing.Any]],
        tool_meta: dict[str, dict[str, typing.Any]],
    ) -> None:
        """在共享 MCP 会话中执行单次请求。"""
        await with_mcp_guard(
            mind,
            runner,
            session=session,
            mode=mode,
            model_api=model_api,
            message=message,
            openai_tools=openai_tools,
            tool_meta=tool_meta,
            **kwargs
        )

    try:
        return await mind.with_mcp_session(model_api, function)
    finally:
        if owns_event_report:
            await event_report.flush()
            await event_report.close()


if __name__ == '__main__':
    pass
