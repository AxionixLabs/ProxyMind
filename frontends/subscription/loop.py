# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import httpx
import asyncio
from websockets.exceptions import (
    ConnectionClosed,
    InvalidStatus,
    WebSocketException,
)
from agent.ports import SubscriptionHost
from observability import (
    observe,
    observe_exception,
)
from .client import AgentClient
from .models import (
    AgentConfig,
    AgentSessionRuntime,
    AgentLiveStatus,
)
from .external_access import publish_external_access
from .opening import (
    normalize_open_payload,
    open_runtime,
    is_tls_certificate_error,
    summarize_tls_certificate_error,
)
from .ws import (
    AckCallback,
    AgentWsProtocolError,
    ConnectedCallback,
    DisconnectedCallback,
    ReadyCallback,
    sleep_or_stop,
    connect_once,
)
from .forwarding import AgentForwardHandler


def summarize_ws_disconnect(exc: BaseException) -> tuple[str, str]:
    """把 WS 断链异常映射成更可读的状态标题和细节。"""
    if is_tls_certificate_error(exc):
        return "TLS Verification Failed", summarize_tls_certificate_error(exc)

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


def get_disconnect_status_code(exc: BaseException) -> int | None:
    """提取断链异常里的 HTTP 状态码，便于恢复链路定位。"""
    if not isinstance(exc, InvalidStatus):
        return None

    response    = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)

    return status_code if isinstance(status_code, int) else None


def should_retry_ws_before_resume(runtime: AgentSessionRuntime, exc: BaseException) -> bool:
    """首轮握手尚未 ready 时，优先做有限次 WS 重连，避免无意义 resume 风暴。"""
    if runtime.last_acked_seq > 0 or runtime.ready_received:
        return False

    if isinstance(exc, InvalidStatus):
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        if isinstance(status_code, int) and status_code in {401, 403}:
            return False

    return True


class AgentConnection(object):
    """管理订阅会话打开、恢复和单次 WS 连接。"""

    def __init__(
        self,
        mind: SubscriptionHost,
        client: AgentClient,
        config: AgentConfig,
        live_status: AgentLiveStatus,
        forward_handler: AgentForwardHandler | None = None,
        *,
        on_ready: ReadyCallback | None = None,
        on_connected: ConnectedCallback | None = None,
        on_disconnected: DisconnectedCallback | None = None,
        on_ack: AckCallback | None = None
    ) -> None:
        """保存连接控制所需依赖。"""
        self.mind = mind
        self.client = client
        self.config = config
        self.live_status = live_status
        self.forward_handler = forward_handler
        self.on_ready = on_ready
        self.on_connected = on_connected
        self.on_disconnected = on_disconnected
        self.on_ack = on_ack

    async def open_session_runtime(
        self,
        *,
        previous: AgentSessionRuntime | None = None
    ) -> AgentSessionRuntime:
        """打开订阅会话，并复用本地去重与任务状态。"""
        opened, device_id = await open_runtime(self.client, self.config)

        session_id, ws_token, ws_url, resume_token, credential, mind_call_example = normalize_open_payload(
            self.client, opened
        )

        runtime = AgentSessionRuntime(
            session_id=session_id,
            ws_token=ws_token,
            resume_token=resume_token,
            credential=credential,
            mind_call_example=mind_call_example,
            ws_url=ws_url,
            device_id=device_id,
            client_version=self.config.client_version,
            forwarded_message_ids=None if previous is None else previous.forwarded_message_ids,
        )
        observe(
            "agent.session.opened",
            session_id=session_id,
            agent_id=self.config.agent_id,
            device_id=device_id,
            reopened=previous is not None,
        )
        return runtime

    async def resume_or_open(
        self,
        runtime: AgentSessionRuntime
    ) -> AgentSessionRuntime:
        """恢复订阅会话；不可恢复时再次打开订阅会话。"""
        if not runtime.resume_token:
            raise RuntimeError("resume_token missing")

        self.live_status.update(
            "Attempting Resume", f"session={runtime.session_id}"
        )
        resume_resp = await self.client.resume_session(
            session_id=runtime.session_id,
            resume_token=runtime.resume_token,
            last_acked_seq=runtime.last_acked_seq,
            device_id=runtime.device_id,
            agent_id=self.config.agent_id
        )

        resume_data = self.client.unwrap_data(resume_resp)
        resumable = bool(resume_data.get("resumable"))

        observe(
            "agent.session.resume_status",
            session_id=runtime.session_id,
            resumable=resumable,
            last_acked_seq=runtime.last_acked_seq,
        )

        if resumable:
            session_id, ws_token, ws_url, resume_token, credential, mind_call_example = normalize_open_payload(
                self.client, resume_resp
            )
            self.live_status.update(
                "Resume Succeeded", "Refreshing handshake and reusing session"
            )
            return AgentSessionRuntime(
                session_id=session_id,
                ws_token=ws_token,
                resume_token=resume_token,
                credential=credential or runtime.credential,
                mind_call_example=mind_call_example or runtime.mind_call_example,
                ws_url=ws_url,
                device_id=runtime.device_id,
                client_version=runtime.client_version,
                last_acked_seq=runtime.last_acked_seq,
                ready_received=runtime.ready_received,
                pre_ready_connect_failures=runtime.pre_ready_connect_failures,
                forwarded_message_ids=runtime.forwarded_message_ids,
            )

        self.live_status.update(
            "Resume Expired", "Opening a fresh subscription session"
        )

        reopened = await self.open_session_runtime(previous=runtime)
        observe(
            "agent.session.reopened",
            session_id=reopened.session_id,
            device_id=reopened.device_id,
        )
        return reopened

    async def connect_once(self, runtime: AgentSessionRuntime) -> None:
        """建立一次 WS 连接并处理连接生命周期内的消息。"""
        await connect_once(
            self.mind,
            self.client,
            runtime,
            self.live_status,
            self.forward_handler,
            on_ready=self.on_ready,
            on_connected=self.on_connected,
            on_disconnected=self.on_disconnected,
            on_ack=self.on_ack,
        )


class AgentSupervisor(object):
    """运行订阅连接生命周期和恢复流程。"""

    def __init__(
        self,
        mind: SubscriptionHost,
        connection: AgentConnection,
        live_status: AgentLiveStatus
    ) -> None:
        """保存订阅运行所需依赖。"""
        self.mind = mind
        self.connection = connection
        self.live_status = live_status
        self.runtime: AgentSessionRuntime | None = None

    async def run(self) -> None:
        """运行订阅会话并处理连接恢复。"""
        runtime = self.runtime

        try:
            observe("agent.supervisor.start")
            self.live_status.update(
                "Opening Session", "Requesting /agents/open"
            )

            if runtime is None:
                runtime = await self.connection.open_session_runtime()
            elif runtime.resume_token:
                runtime = await self.connection.resume_or_open(runtime)
            else:
                runtime = await self.connection.open_session_runtime(
                    previous=runtime,
                )
            self.runtime = runtime

            observe("agent.subscription.ready", session_id=runtime.session_id)
            self.live_status.update(
                "Subscription Ready", "Rendering external call example"
            )

            await publish_external_access(runtime)
            self.live_status.update(
                "Waiting for Server Tasks", "Long link established and listening"
            )

            while not self.mind.task_event.is_set():
                try:
                    await self.connection.connect_once(runtime)
                    return None
                except asyncio.CancelledError:
                    self.live_status.update(
                        "Exiting Subscription", "Interrupting network wait"
                    )
                    raise
                except AgentWsProtocolError as exc:
                    runtime = await self.handle_protocol_error(runtime, exc)
                    self.runtime = runtime
                    continue
                except (
                        ConnectionClosed,
                        InvalidStatus,
                        WebSocketException,
                        OSError,
                        httpx.HTTPError,
                        asyncio.TimeoutError
                ) as exc:
                    runtime = await self.handle_disconnect(runtime, exc)
                    self.runtime = runtime
                    continue

        finally:
            self.runtime = runtime
            observe(
                "agent.supervisor.stop",
                session_id=runtime.session_id if runtime is not None else None,
            )
            self.live_status.update(
                "Exiting Subscription", "Closing listener resources"
            )

    async def handle_protocol_error(
        self,
        runtime: AgentSessionRuntime,
        exc: AgentWsProtocolError
    ) -> AgentSessionRuntime:
        """处理服务端协议错误并返回当前运行态。"""
        observe(
            "agent.protocol.error",
            level="WARNING",
            code=exc.code,
            action=exc.action,
            message=exc.message_text,
        )
        if exc.action == "abort":
            self.live_status.update(
                "Protocol Rejected", f"{exc.code} · stopping subscription"
            )
            raise exc

        if exc.action == "reopen":
            self.live_status.update(
                "Server Rejected Session", f"{exc.code} · opening fresh session"
            )
            try:
                runtime = await self.connection.open_session_runtime(previous=runtime)
            except asyncio.CancelledError:
                self.live_status.update(
                    "Exiting Subscription", "Canceling reopen flow"
                )
                raise
            except (OSError, httpx.HTTPError, asyncio.TimeoutError) as reopen_exc:
                if is_tls_certificate_error(reopen_exc):
                    detail = summarize_tls_certificate_error(reopen_exc)
                    observe_exception(
                        "agent.reopen.failed",
                        reopen_exc,
                        reason="tls",
                        detail=detail,
                    )
                    self.live_status.update("TLS Verification Failed", detail)
                    raise
                observe_exception("agent.reopen.retry", reopen_exc, level="WARNING")
                self.live_status.update(
                    "Reopen Failed", f"{type(reopen_exc).__name__} · retrying in 2s"
                )
                await sleep_or_stop(2.0, self.mind.task_event)
                return runtime
            except Exception as reopen_exc:
                observe_exception("agent.reopen.retry", reopen_exc, level="WARNING")
                self.live_status.update(
                    "Reopen Crashed", f"{type(reopen_exc).__name__} · retrying in 2s"
                )
                await sleep_or_stop(2.0, self.mind.task_event)
                return runtime

            await publish_external_access(runtime)
            self.live_status.update(
                "Reopened and Waiting", "Returning to listening state in 1s"
            )
            await sleep_or_stop(1.0, self.mind.task_event)
            return runtime

        self.live_status.update(
            "Reconnecting", f"{exc.code} · reconnecting in 1s"
        )
        await sleep_or_stop(1.0, self.mind.task_event)
        return runtime

    async def handle_disconnect(
        self,
        runtime: AgentSessionRuntime,
        exc: BaseException
    ) -> AgentSessionRuntime:
        """处理连接中断并返回当前运行态。"""
        if is_tls_certificate_error(exc):
            detail = summarize_tls_certificate_error(exc)
            observe_exception(
                "agent.disconnected",
                exc,
                reason="tls",
                detail=detail,
            )
            self.live_status.update("TLS Verification Failed", detail)
            raise exc

        observe_exception("agent.disconnected", exc, level="WARNING")

        title, detail = summarize_ws_disconnect(exc)
        self.live_status.update(title, detail)

        status_code = get_disconnect_status_code(exc)

        observe(
            "agent.recovery.state",
            session_id=runtime.session_id,
            ready_received=runtime.ready_received,
            last_acked_seq=runtime.last_acked_seq,
            pre_ready_failures=runtime.pre_ready_connect_failures,
            resume_available=bool(runtime.resume_token),
            status=status_code,
        )

        retry_ws_before_resume = should_retry_ws_before_resume(runtime, exc)
        observe(
            "agent.recovery.decision",
            action="ws_retry" if retry_ws_before_resume else "resume_or_reopen",
            reason="pre_ready" if retry_ws_before_resume else "ready_or_acked",
        )

        if retry_ws_before_resume:
            return await self.handle_pre_ready_disconnect(runtime)

        if not runtime.resume_token:
            observe(
                "agent.resume.skipped",
                level="WARNING",
                session_id=runtime.session_id,
                reason="token_missing",
                ready_received=runtime.ready_received,
                last_acked_seq=runtime.last_acked_seq,
            )
            self.live_status.update(
                "Resume Token Missing", "Retrying session open in 2s"
            )
            await sleep_or_stop(2.0, self.mind.task_event)
            return runtime

        return await self.handle_resume(runtime)

    async def handle_pre_ready_disconnect(
        self,
        runtime: AgentSessionRuntime
    ) -> AgentSessionRuntime:
        """处理 ready 前的连接中断。"""
        runtime.pre_ready_connect_failures += 1

        observe(
            "agent.pre_ready.retry",
            session_id=runtime.session_id,
            attempt=runtime.pre_ready_connect_failures,
        )

        if runtime.pre_ready_connect_failures < 3:
            self.live_status.update(
                "Retrying Link", "Handshake not ready yet · retrying WS in 2s"
            )
            await sleep_or_stop(2.0, self.mind.task_event)
            return runtime

        self.live_status.update(
            "Opening Fresh Session", "Handshake never became ready · reopening in 2s"
        )
        try:
            runtime = await self.connection.open_session_runtime(previous=runtime)
        except asyncio.CancelledError:
            self.live_status.update(
                "Exiting Subscription", "Canceling session reopen"
            )
            raise
        except (OSError, httpx.HTTPError, asyncio.TimeoutError) as reopen_exc:
            if is_tls_certificate_error(reopen_exc):
                detail = summarize_tls_certificate_error(reopen_exc)
                observe_exception(
                    "agent.pre_ready.reopen_failed",
                    reopen_exc,
                    reason="tls",
                    detail=detail,
                )
                self.live_status.update("TLS Verification Failed", detail)
                raise
            observe_exception("agent.pre_ready.reopen_retry", reopen_exc, level="WARNING")
            self.live_status.update(
                "Reopen Failed", f"{type(reopen_exc).__name__} · retrying in 2s"
            )
            await sleep_or_stop(2.0, self.mind.task_event)
            return runtime
        except Exception as reopen_exc:
            observe_exception("agent.pre_ready.reopen_retry", reopen_exc, level="WARNING")
            self.live_status.update(
                "Reopen Crashed", f"{type(reopen_exc).__name__} · retrying in 2s"
            )
            await sleep_or_stop(2.0, self.mind.task_event)
            return runtime

        await publish_external_access(runtime)
        self.live_status.update(
            "Reopened and Waiting", "Returning to listening state in 1s"
        )
        await sleep_or_stop(1.0, self.mind.task_event)

        return runtime

    async def handle_resume(
        self,
        runtime: AgentSessionRuntime
    ) -> AgentSessionRuntime:
        """处理连接恢复请求。"""
        try:
            observe(
                "agent.resume.start",
                session_id=runtime.session_id,
                last_acked_seq=runtime.last_acked_seq,
                ready_received=runtime.ready_received,
            )
            runtime = await self.connection.resume_or_open(runtime)
            observe(
                "agent.resume.complete",
                session_id=runtime.session_id,
                last_acked_seq=runtime.last_acked_seq,
                ready_received=runtime.ready_received,
                resume_available=bool(runtime.resume_token),
            )
        except asyncio.CancelledError:
            self.live_status.update(
                "Exiting Subscription", "Canceling resume flow"
            )
            raise
        except (OSError, httpx.HTTPError, asyncio.TimeoutError) as resume_exc:
            if is_tls_certificate_error(resume_exc):
                detail = summarize_tls_certificate_error(resume_exc)
                observe_exception(
                    "agent.resume.failed",
                    resume_exc,
                    reason="tls",
                    detail=detail,
                )
                self.live_status.update("TLS Verification Failed", detail)
                raise
            observe_exception("agent.resume.retry", resume_exc, level="WARNING")
            self.live_status.update(
                "Resume Failed", f"{type(resume_exc).__name__} · retrying in 2s"
            )
            await sleep_or_stop(2.0, self.mind.task_event)
            return runtime
        except Exception as resume_exc:
            observe_exception("agent.resume.retry", resume_exc, level="WARNING")
            self.live_status.update(
                "Resume Crashed", f"{type(resume_exc).__name__} · retrying in 2s"
            )
            await sleep_or_stop(2.0, self.mind.task_event)
            return runtime

        self.live_status.update(
            "Resumed and Waiting", "Returning to listening state in 1s"
        )
        await publish_external_access(runtime)
        await sleep_or_stop(1.0, self.mind.task_event)
        return runtime


if __name__ == '__main__':
    pass
