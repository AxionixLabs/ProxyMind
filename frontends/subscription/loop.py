# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio

import httpx
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
from .forwarding import AgentForwardHandler
from .models import (
    AgentConfig,
    AgentSessionRuntime,
    AgentLiveStatus,
)
from .opening import (
    extract_http_error_detail,
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


def summarize_ws_disconnect(exc: BaseException) -> tuple[str, str]:
    """把 WS 断链异常映射成更可读的状态标题和细节。"""
    if is_tls_certificate_error(exc):
        return "TLS Verification Failed", summarize_tls_certificate_error(exc)

    if isinstance(exc, InvalidStatus):
        status_code = exc.response.status_code
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

    return exc.response.status_code


class AgentConnection(object):
    """管理订阅会话打开、恢复和单次 WS 连接。"""

    def __init__(
        self,
        host: SubscriptionHost,
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
        self.host = host
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

        session_id, ws_token, ws_url, resume_token, credential, web_url = normalize_open_payload(
            self.client, opened
        )

        runtime = AgentSessionRuntime(
            session_id=session_id,
            ws_token=ws_token,
            resume_token=resume_token,
            credential=credential,
            web_url=web_url,
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
        """换取新握手令牌，并在恢复凭据失效时重新创建订阅会话。"""
        runtime.ws_token = None
        if not runtime.resume_token:
            return await self.open_session_runtime(previous=runtime)

        self.live_status.update(
            "Attempting Resume", f"session={runtime.session_id}"
        )
        try:
            resume_resp = await self.client.resume_session(
                session_id=runtime.session_id,
                resume_token=runtime.resume_token,
                last_acked_seq=runtime.last_acked_seq,
                device_id=runtime.device_id,
                agent_id=self.config.agent_id,
            )
        except httpx.HTTPStatusError as error:
            code = extract_http_error_detail(error).get("code")
            invalid_resume = error.response.status_code == 401 and code in {
                "AGENT_TOKEN_INVALID", "AGENT_RESUME_SCOPE_INVALID",
                "AGENT_RESUME_SESSION_MISMATCH", "AGENT_RESUME_DEVICE_MISMATCH",
            }
            missing_session = error.response.status_code == 404 and code == "AGENT_SESSION_NOT_FOUND"
            if not invalid_resume and not missing_session:
                raise
            runtime.resume_token = None
            self.live_status.update("Resume Expired", "Opening a fresh subscription session")
            return await self.open_session_runtime(previous=runtime)

        resume_data = self.client.unwrap_data(resume_resp)
        resumable = bool(resume_data.get("resumable"))

        observe(
            "agent.session.resume_status",
            session_id=runtime.session_id,
            resumable=resumable,
            last_acked_seq=runtime.last_acked_seq,
        )

        if resumable:
            session_id, ws_token, ws_url, resume_token, credential, web_url = normalize_open_payload(
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
                web_url=web_url,
                ws_url=ws_url,
                device_id=runtime.device_id,
                client_version=runtime.client_version,
                last_acked_seq=runtime.last_acked_seq,
                ready_received=runtime.ready_received,
                forwarded_message_ids=runtime.forwarded_message_ids,
            )

        self.live_status.update(
            "Resume Expired", "Opening a fresh subscription session"
        )
        runtime.resume_token = None
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
            self.host,
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
        host: SubscriptionHost,
        connection: AgentConnection,
        live_status: AgentLiveStatus,
    ) -> None:
        """保存订阅运行所需依赖。"""
        self.host = host
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
            else:
                runtime = await self.handle_resume(runtime)
            self.runtime = runtime

            observe("agent.subscription.ready", session_id=runtime.session_id)
            self.live_status.update(
                "Subscription Ready", "Preparing device connection details"
            )

            self.live_status.update(
                "Waiting for Server Tasks", "Long link established and listening"
            )

            while not self.host.lifecycle.stop_event.is_set():
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
            runtime.resume_token = None

        self.live_status.update(
            "Reconnecting", f"{exc.code} · reconnecting in 1s"
        )
        await sleep_or_stop(1.0, self.host.lifecycle.stop_event)
        return await self.handle_resume(runtime)

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
            resume_available=bool(runtime.resume_token),
            status=status_code,
        )

        observe(
            "agent.recovery.decision",
            action="resume_or_reopen",
            reason="connection_closed",
        )
        runtime.ws_token = None
        await sleep_or_stop(2.0, self.host.lifecycle.stop_event)
        return await self.handle_resume(runtime)

    async def handle_resume(
        self,
        runtime: AgentSessionRuntime
    ) -> AgentSessionRuntime:
        """串行恢复到可握手状态，临时失败按有界退避重试并响应退出。"""
        runtime.ws_token = None
        retry_delay = 2.0
        while not self.host.lifecycle.stop_event.is_set():
            try:
                observe(
                    "agent.resume.start",
                    session_id=runtime.session_id,
                    last_acked_seq=runtime.last_acked_seq,
                )
                recovered = await self.connection.resume_or_open(runtime)
            except httpx.HTTPStatusError as error:
                status = error.response.status_code
                if status not in {408, 409, 429} and not 500 <= status < 600:
                    self.live_status.update(
                        "Recovery Rejected", f"HTTP {status} · check authentication or server configuration"
                    )
                    raise
                observe_exception("agent.resume.retry", error, level="WARNING", status=status)
            except (OSError, httpx.RequestError, asyncio.TimeoutError) as error:
                if is_tls_certificate_error(error):
                    self.live_status.update("TLS Verification Failed", summarize_tls_certificate_error(error))
                    raise
                observe_exception("agent.resume.retry", error, level="WARNING")
            else:
                observe(
                    "agent.resume.complete",
                    session_id=recovered.session_id,
                    last_acked_seq=recovered.last_acked_seq,
                    resume_available=bool(recovered.resume_token),
                )
                return recovered

            self.live_status.update("Recovery Pending", f"Retrying session recovery in {retry_delay:g}s")
            await sleep_or_stop(retry_delay, self.host.lifecycle.stop_event)
            retry_delay = min(retry_delay * 2, 30.0)

        raise asyncio.CancelledError


if __name__ == '__main__':
    pass
