# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import httpx
import typing
import asyncio
import platform
from loguru import logger
from websockets.exceptions import ConnectionClosed, InvalidStatus, WebSocketException
from ...runtime.agent_client import AgentClient
from .models import (
    AgentConfig,
    AgentSessionRuntime,
    AgentLiveStatus
)
from .ui import (
    start_connect_animation,
    start_status_animation,
    log_external_access
)
from .opening import (
    build_device_id,
    normalize_open_payload,
    open_runtime
)
from .ws import (
    AgentWsProtocolError,
    cancel_runtime_tasks,
    sleep_or_stop,
    connect_once
)
from mind_nova import const

if typing.TYPE_CHECKING:
    from ...mind_core import Mind


def summarize_ws_disconnect(exc: BaseException) -> tuple[str, str]:
    """把 WS 断链异常映射成更可读的状态标题和细节。"""
    if isinstance(exc, InvalidStatus):
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        if isinstance(status_code, int):
            if status_code in {502, 503, 504}:
                return "Server Unavailable", f"HTTP {status_code} · retrying session"
            if status_code in {401, 403}:
                return "Handshake Rejected", f"HTTP {status_code} · checking auth or session"
            return "Handshake Rejected", f"HTTP {status_code} · preparing reconnect"
        return "Handshake Rejected", "WS upgrade failed · preparing reconnect"

    if isinstance(exc, ConnectionClosed):
        return "Link Interrupted", f"{type(exc).__name__} · preparing reconnect"

    if isinstance(exc, WebSocketException):
        return "WebSocket Error", f"{type(exc).__name__} · preparing reconnect"

    if isinstance(exc, asyncio.TimeoutError):
        return "Connection Timeout", "Timed out · preparing reconnect"

    if isinstance(exc, httpx.HTTPError):
        return "HTTP Error", f"{type(exc).__name__} · preparing reconnect"

    if isinstance(exc, OSError):
        return "Network Error", f"{type(exc).__name__} · preparing reconnect"

    return "Link Interrupted", f"{type(exc).__name__} · preparing reconnect"


async def resume_or_reopen(
    client: AgentClient,
    runtime: AgentSessionRuntime,
    config: AgentConfig,
    live_status: AgentLiveStatus
) -> AgentSessionRuntime:
    """优先尝试 resume；如果服务端判定不可恢复，则重新打开新会话。"""
    if not runtime.resume_token:
        raise RuntimeError("resume_token missing")

    live_status.update(
        "Attempting Resume", f"session={runtime.session_id}"
    )
    resume_resp = await client.resume_session(
        session_id=runtime.session_id,
        resume_token=runtime.resume_token,
        last_acked_seq=runtime.last_acked_seq,
        device_id=runtime.device_id,
        agent_id=config.agent_id
    )

    resume_data = client.unwrap_data(resume_resp)
    resumable   = bool(resume_data.get("resumable"))

    logger.debug(
        f"[Agent] resume status resumable={resumable} last_acked_seq={runtime.last_acked_seq}"
    )

    if resumable:
        session_id, ws_token, ws_url, resume_token, access_token, mind_call_example = normalize_open_payload(
            client, resume_resp
        )
        live_status.update(
            "Resume Succeeded", "Refreshing handshake and reusing session"
        )
        return AgentSessionRuntime(
            session_id=session_id,
            ws_token=ws_token,
            resume_token=resume_token,
            access_token=access_token or runtime.access_token,
            mind_call_example=mind_call_example or runtime.mind_call_example,
            ws_url=ws_url,
            device_id=runtime.device_id,
            client_version=runtime.client_version,
            last_acked_seq=runtime.last_acked_seq,
            forwarded_message_ids=runtime.forwarded_message_ids,
            pending_tasks=runtime.pending_tasks
        )

    live_status.update(
        "Resume Expired", "Opening a fresh subscription session"
    )

    reopened = await open_new_runtime(client, config, previous=runtime)
    logger.debug(
        f"[Agent] reopened session_id={reopened.session_id} device_id={reopened.device_id}"
    )
    log_external_access(reopened)
    return reopened


async def open_new_runtime(
    client: AgentClient,
    config: AgentConfig,
    *,
    previous: AgentSessionRuntime | None = None
) -> AgentSessionRuntime:
    """打开一个全新的订阅会话，并尽量复用本地去重与任务状态。"""
    opened, device_id = await open_runtime(client, config)

    session_id, ws_token, ws_url, resume_token, access_token, mind_call_example = normalize_open_payload(client, opened)

    runtime = AgentSessionRuntime(
        session_id=session_id,
        ws_token=ws_token,
        resume_token=resume_token,
        access_token=access_token,
        mind_call_example=mind_call_example,
        ws_url=ws_url,
        device_id=device_id,
        client_version=config.client_version,
        forwarded_message_ids=None if previous is None else previous.forwarded_message_ids,
        pending_tasks=None if previous is None else previous.pending_tasks
    )
    logger.debug(
        f"[Agent] opened session_id={session_id} agent_id={config.agent_id} device_id={device_id}"
    )
    return runtime


async def agent_loop(mind: "Mind") -> None:
    """订阅模式主循环：创建会话、建立 WS，并持续处理协议消息。"""
    config = AgentConfig(
        base_url=const.DOMAIN,
        device_id=build_device_id(),
        agent_id=const.APP_NAME,
        client_version=const.APP_VERSION,
        platform=(platform.system().strip().lower() or "unknown"),
        arch=(platform.machine().strip().lower() or "unknown")
    )

    client      = AgentClient(base_url=config.base_url)
    live_status = AgentLiveStatus()

    runtime: AgentSessionRuntime | None = None

    try:
        await start_connect_animation(mind, live_status)

        live_status.update(
            "Opening Session", "Requesting /agents/open"
        )

        runtime = await open_new_runtime(client, config)

        logger.debug(
            f"[Agent] {config.base_url}"
        )
        live_status.update(
            "Subscription Ready", "Rendering external call example"
        )

        await mind.await_cleanup(mind.stop_anim())
        log_external_access(runtime)

        if not mind.task_event.is_set():
            await start_status_animation(mind, live_status)
            live_status.update(
                "Waiting for Server Tasks", "Long link established and listening"
            )

        while not mind.task_event.is_set():
            try:
                await connect_once(mind, client, runtime, live_status)
                return None
            except asyncio.CancelledError:
                live_status.update(
                    "Exiting Subscription", "Interrupting network wait"
                )
                raise
            except AgentWsProtocolError as exc:
                logger.debug(
                    f"[Agent] ws protocol error code={exc.code} action={exc.action} message={exc.message_text}"
                )
                if exc.action == "abort":
                    live_status.update(
                        "Protocol Rejected", f"{exc.code} · stopping subscription"
                    )
                    raise

                if exc.action == "reopen":
                    live_status.update(
                        "Server Rejected Session", f"{exc.code} · opening fresh session"
                    )
                    try:
                        runtime = await open_new_runtime(client, config, previous=runtime)
                    except asyncio.CancelledError:
                        live_status.update(
                            "Exiting Subscription", "Canceling reopen flow"
                        )
                        raise
                    except (OSError, httpx.HTTPError, asyncio.TimeoutError) as reopen_exc:
                        logger.debug(
                            f"[Agent] reopen failed: {type(reopen_exc).__name__}: {reopen_exc}"
                        )
                        live_status.update(
                            "Reopen Failed", f"{type(reopen_exc).__name__} · retrying in 2s"
                        )
                        await sleep_or_stop(2.0, mind.task_event)
                        continue
                    except Exception as reopen_exc:
                        logger.debug(
                            f"[Agent] reopen crashed: {type(reopen_exc).__name__}: {reopen_exc}"
                        )
                        live_status.update(
                            "Reopen Crashed", f"{type(reopen_exc).__name__} · retrying in 2s"
                        )
                        await sleep_or_stop(2.0, mind.task_event)
                        continue

                    log_external_access(runtime)
                    live_status.update(
                        "Reopened and Waiting", "Returning to listening state in 1s"
                    )
                    await sleep_or_stop(1.0, mind.task_event)
                    continue

                live_status.update(
                    "Reconnecting", f"{exc.code} · reconnecting in 1s"
                )
                await sleep_or_stop(1.0, mind.task_event)
                continue
            except (ConnectionClosed, InvalidStatus, WebSocketException, OSError, httpx.HTTPError, asyncio.TimeoutError) as exc:
                logger.debug(
                    f"[Agent] disconnected: {type(exc).__name__}: {exc}"
                )
                title, detail = summarize_ws_disconnect(exc)
                live_status.update(title, detail)

                if not runtime.resume_token:
                    logger.debug(
                        "[Agent] resume skipped: resume_token missing"
                    )
                    live_status.update(
                        "Resume Token Missing", "Retrying session open in 2s"
                    )
                    await sleep_or_stop(2.0, mind.task_event)
                    continue

                try:
                    runtime = await resume_or_reopen(client, runtime, config, live_status)
                except asyncio.CancelledError:
                    live_status.update(
                        "Exiting Subscription", "Canceling resume flow"
                    )
                    raise
                except (OSError, httpx.HTTPError, asyncio.TimeoutError) as resume_exc:
                    logger.debug(
                        f"[Agent] resume failed: {type(resume_exc).__name__}: {resume_exc}"
                    )
                    live_status.update(
                        "Resume Failed", f"{type(resume_exc).__name__} · retrying in 2s"
                    )
                    await sleep_or_stop(2.0, mind.task_event)
                    continue
                except Exception as resume_exc:
                    logger.debug(
                        f"[Agent] resume crashed: {type(resume_exc).__name__}: {resume_exc}"
                    )
                    live_status.update(
                        "Resume Crashed", f"{type(resume_exc).__name__} · retrying in 2s"
                    )
                    await sleep_or_stop(2.0, mind.task_event)
                    continue

                live_status.update(
                    "Resumed and Waiting", "Returning to listening state in 1s"
                )
                await sleep_or_stop(1.0, mind.task_event)
    finally:
        live_status.update(
            "Exiting Subscription", "Cleaning tasks and stopping animation"
        )
        if runtime is not None:
            await cancel_runtime_tasks(runtime)
        await mind.await_cleanup(mind.stop_anim())


async def run_agent_loop(mind: "Mind") -> None:
    return await agent_loop(mind)


if __name__ == '__main__':
    pass
