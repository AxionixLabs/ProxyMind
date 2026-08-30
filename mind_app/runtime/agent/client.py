# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import ssl
import json
import httpx
import socket
import typing
import certifi
import websockets
from urllib.parse import urlencode
from websockets.asyncio.client import ClientConnection
from .protocol import (
    build_envelope, ensure_ws_base
)
from metadata import const
from protocol.transport import config


class AgentClient(object):
    """`/agents` 协议使用的 HTTP + WS 订阅客户端。"""

    def __init__(
        self,
        *,
        base_url: str,
        timeout_sec: float = 15.0
    ) -> None:
        """初始化订阅客户端的基础地址和超时时间。"""
        self.base_url = base_url.rstrip("/")
        self.timeout_sec = timeout_sec

    @staticmethod
    def _certifi_path() -> str:
        """返回当前运行时 `certifi` 解析出的 CA bundle 路径。"""
        return certifi.where()

    @staticmethod
    def _build_ssl_context(cafile: str) -> ssl.SSLContext:
        """构建显式绑定 `certifi` 证书包的 SSL context。"""
        return ssl.create_default_context(cafile=cafile)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        token_kind: typing.Literal["client", "admin"],
        json_body: typing.Any = None,
        params: dict[str, typing.Any] | None = None
    ) -> dict[str, typing.Any]:
        """用指定身份令牌发起 HTTP 请求，并统一返回 JSON 响应。"""
        timeout = httpx.Timeout(
            connect=self.timeout_sec,
            read=max(30.0, self.timeout_sec),
            write=self.timeout_sec,
            pool=self.timeout_sec,
        )

        if token_kind == "client":
            header_name = "X-Agent-Token"
            token_value = config.AGENT_CLIENT_SECRET
        else:
            header_name = "X-Agent-Admin-Token"
            token_value = config.AGENT_ADMIN_SECRET

        verify: str | bool = True
        if self.base_url.startswith("https://"):
            cafile = self._certifi_path()
            verify = cafile

        async with httpx.AsyncClient(timeout=timeout, verify=verify) as http:
            response = await http.request(
                method=method,
                url=f"{self.base_url}{path}",
                headers={
                    header_name: token_value,
                    "Content-Type": "application/json",
                },
                json=json_body,
                params=params
            )

        response.raise_for_status()

        if not response.content:
            return {}

        return typing.cast(dict[str, typing.Any], response.json())

    async def open_session(
        self,
        *,
        device_id: str,
        agent_id: str,
        client_version: str,
        platform: str,
        arch: str,
        hostname: str | None = None,
    ) -> dict[str, typing.Any]:
        """调用 `/agents/open` 创建新的订阅会话。"""
        payload = {
            "device_id"      : device_id,
            "agent_id"       : agent_id,
            "client_version" : client_version,
            "platform"       : platform,
            "arch"           : arch,
            "hostname"       : hostname or socket.gethostname(),
        }
        return await self._request(
            method="POST", path="/agents/open", token_kind="client", json_body=payload
        )

    async def resume_session(
        self,
        *,
        session_id: str,
        resume_token: str,
        last_acked_seq: int,
        device_id: str | None = None,
        agent_id: str | None = None,
    ) -> dict[str, typing.Any]:
        """调用 `/agents/resume` 恢复已存在的订阅会话。"""
        payload: dict[str, typing.Any] = {
            "session_id"     : session_id,
            "resume_token"   : resume_token,
            "last_acked_seq" : last_acked_seq
        }
        if device_id:
            payload["device_id"] = device_id
        if agent_id:
            payload["agent_id"] = agent_id

        return await self._request(
            method="POST", path="/agents/resume", token_kind="client", json_body=payload
        )

    @staticmethod
    def unwrap_data(payload: dict[str, typing.Any]) -> dict[str, typing.Any]:
        """当服务端把协议体包在顶层 `data` 字段时，取出真实内容。"""
        data = payload.get("data")
        if isinstance(data, dict):
            return data
        return payload

    @staticmethod
    def build_selector(
        selector: dict[str, typing.Any] | None = None,
        *,
        session_id: str | None = None,
        agent_session_id: str | None = None,
        agent_id: str | None = None,
        device_id: str | None = None,
        session_strategy: str | None = None,
    ) -> dict[str, typing.Any]:
        """构造管理面使用的 `selector` 负载。"""
        merged = dict(selector or {})

        if session_id:
            merged["session_id"] = session_id
        if agent_session_id:
            merged["agent_session_id"] = agent_session_id
        if agent_id:
            merged["agent_id"] = agent_id
        if device_id:
            merged["device_id"] = device_id
        if session_strategy:
            merged["session_strategy"] = session_strategy

        return merged

    @classmethod
    def build_selector_params(
        cls,
        selector: dict[str, typing.Any] | None = None,
        **kwargs: typing.Any
    ) -> dict[str, typing.Any]:
        """把 `selector` 编码成 GET 查询参数。"""
        merged = cls.build_selector(selector, **kwargs)
        return {
            f"selector.{key}" : value
            for key, value in merged.items()
            if value not in (None, "")
        }

    def build_ws_url(self, *, session_id: str, ws_token: str, ws_base_url: str | None = None) -> str:
        """拼出 `/agents/ws` 连接地址，并处理 HTTP/WS 协议前缀转换。"""
        query  = urlencode({"session_id": session_id, "ws_token": ws_token})
        source = (ws_base_url or self.base_url).rstrip("/")

        if source.startswith("ws://") or source.startswith("wss://"):
            if self.base_url.startswith("https://") and source.startswith("ws://"):
                source = "wss://" + source[len("ws://") :]
        else:
            source = ensure_ws_base(source)

        if source.endswith("/agents/ws"):
            return f"{source}?{query}"

        return f"{source}/agents/ws?{query}"

    async def connect_ws(
        self,
        *,
        session_id: str,
        ws_token: str,
        ws_base_url: str | None = None
    ) -> ClientConnection:
        """建立订阅协议的 WebSocket 连接。"""
        url = self.build_ws_url(
            session_id=session_id,
            ws_token=ws_token,
            ws_base_url=ws_base_url
        )
        connect_kwargs: dict[str, typing.Any] = {
            "open_timeout"  : self.timeout_sec,
            "close_timeout" : 1.0,
            "ping_interval" : None
        }

        if url.startswith("wss://"):
            cafile = self._certifi_path()
            connect_kwargs["ssl"] = self._build_ssl_context(cafile)

        return await websockets.connect(url, **connect_kwargs)

    @staticmethod
    async def send_json(connection: ClientConnection, envelope: dict[str, typing.Any]) -> None:
        """把协议信封编码成 JSON 并发送到当前连接。"""
        await connection.send(json.dumps(envelope, ensure_ascii=False))

    @staticmethod
    async def recv_json(connection: ClientConnection) -> dict[str, typing.Any]:
        """从当前连接接收一条 JSON 消息，并按项目字符集解码。"""
        raw = await connection.recv()
        if isinstance(raw, bytes):
            raw = raw.decode(const.CHARSET)
        return typing.cast(dict[str, typing.Any], json.loads(raw))

    async def send_hello(
        self,
        connection: ClientConnection,
        *,
        session_id: str,
        device_id: str,
        client_version: str
    ) -> None:
        """发送首次握手用的 `hello` 消息。"""
        await self.send_json(
            connection,
            build_envelope(
                message_type="hello",
                session_id=session_id,
                payload={
                    "client_version" : client_version,
                    "device_id"      : device_id
                }
            )
        )

    async def send_runtime_bind(
        self,
        connection: ClientConnection,
        *,
        session_id: str,
        llm_conf: dict[str, typing.Any]
    ) -> None:
        """发送 `runtime.bind`，上报当前会话默认模型配置。"""
        await self.send_json(
            connection,
            build_envelope(
                message_type="runtime.bind",
                session_id=session_id,
                payload={"llm_conf": llm_conf}
            )
        )

    async def send_resume(
        self,
        connection: ClientConnection,
        *,
        session_id: str,
        last_acked_seq: int
    ) -> None:
        """发送 `resume` 消息，告知服务端本地已确认到的序号。"""
        await self.send_json(
            connection,
            build_envelope(
                message_type="resume",
                session_id=session_id,
                payload={"last_acked_seq": last_acked_seq}
            )
        )

    async def send_pong(self, connection: ClientConnection, *, session_id: str) -> None:
        """回复服务端 `ping`，维持连接心跳。"""
        await self.send_json(connection, build_envelope("pong", session_id))

    async def send_mind_received(
        self,
        connection: ClientConnection,
        *,
        session_id: str,
        cid: str,
        sid: str,
        call_id: str,
        acked_message_id: str,
        disposition: typing.Literal["queued", "auto_run"]
    ) -> None:
        """发送 `mind.received`，确认已收到指定 `mind.forward`。"""
        await self.send_json(
            connection,
            build_envelope(
                message_type="mind.received",
                session_id=session_id,
                cid=cid,
                sid=sid,
                payload={
                    "call_id": call_id,
                    "acked_message_id": acked_message_id,
                    "disposition": disposition
                }
            )
        )

    async def send_mind_started(
        self,
        connection: ClientConnection,
        *,
        session_id: str,
        cid: str,
        sid: str,
        call_id: str
    ) -> None:
        """发送 `mind.started`，告知服务端本地已开始执行任务。"""
        await self.send_json(
            connection,
            build_envelope(
                message_type="mind.started",
                session_id=session_id,
                cid=cid,
                sid=sid,
                payload={"call_id": call_id}
            )
        )

    async def send_mind_completed(
        self,
        connection: ClientConnection,
        *,
        session_id: str,
        cid: str,
        sid: str,
        call_id: str
    ) -> None:
        """发送 `mind.completed`，告知服务端本地已完成任务。"""
        await self.send_json(
            connection,
            build_envelope(
                message_type="mind.completed",
                session_id=session_id,
                cid=cid,
                sid=sid,
                payload={"call_id": call_id}
            )
        )

    async def send_mind_failed(
        self,
        connection: ClientConnection,
        *,
        session_id: str,
        cid: str,
        sid: str,
        call_id: str,
        error_type: str,
        error_message: str
    ) -> None:
        """发送 `mind.failed`，告知服务端本地任务失败。"""
        await self.send_json(
            connection,
            build_envelope(
                message_type="mind.failed",
                session_id=session_id,
                cid=cid,
                sid=sid,
                payload={
                    "call_id": call_id,
                    "error": {
                        "type"    : error_type,
                        "message" : error_message
                    }
                }
            )
        )

    async def send_mind_cancelled(
        self,
        connection: ClientConnection,
        *,
        session_id: str,
        cid: str,
        sid: str,
        call_id: str,
        reason: typing.Literal[
            "user_interrupted",
            "message_deleted",
            "client_shutdown"
        ]
    ) -> None:
        """发送 `mind.cancelled`，告知服务端任务已被本地取消。"""
        await self.send_json(
            connection,
            build_envelope(
                message_type="mind.cancelled",
                session_id=session_id,
                cid=cid,
                sid=sid,
                payload={
                    "call_id": call_id,
                    "reason": reason
                }
            )
        )

    async def get_status(
        self,
        *,
        selector: dict[str, typing.Any] | None = None,
        session_id: str | None = None,
        agent_session_id: str | None = None,
        agent_id: str | None = None,
        device_id: str | None = None,
        session_strategy: str | None = None
    ) -> dict[str, typing.Any]:
        """调用 `/agents/status` 查询指定会话状态。"""
        return await self._request(
            method="GET",
            path="/agents/status",
            token_kind="admin",
            params=self.build_selector_params(
                selector,
                session_id=session_id,
                agent_session_id=agent_session_id,
                agent_id=agent_id,
                device_id=device_id,
                session_strategy=session_strategy,
            )
        )

    async def list_sessions(
        self,
        *,
        selector: dict[str, typing.Any] | None = None,
        session_id: str | None = None,
        agent_session_id: str | None = None,
        agent_id: str | None = None,
        device_id: str | None = None,
        session_strategy: str | None = None
    ) -> dict[str, typing.Any]:
        """调用 `/agents/sessions` 查询会话列表。"""
        return await self._request(
            method="GET",
            path="/agents/sessions",
            token_kind="admin",
            params=self.build_selector_params(
                selector,
                session_id=session_id,
                agent_session_id=agent_session_id,
                agent_id=agent_id,
                device_id=device_id,
                session_strategy=session_strategy,
            )
        )

    async def close_session(
        self,
        *,
        selector: dict[str, typing.Any] | None = None,
        session_id: str | None = None,
        agent_session_id: str | None = None,
        agent_id: str | None = None,
        device_id: str | None = None,
        session_strategy: str | None = None,
        reason: str | None = None,
        force: bool | None = None
    ) -> dict[str, typing.Any]:
        """调用 `/agents/close` 关闭指定会话。"""
        payload: dict[str, typing.Any] = {
            "selector" : self.build_selector(
                selector,
                session_id=session_id,
                agent_session_id=agent_session_id,
                agent_id=agent_id,
                device_id=device_id,
                session_strategy=session_strategy,
            )
        }

        if reason:
            payload["reason"] = reason
        if force is not None:
            payload["force"] = force

        return await self._request(
            method="POST",
            path="/agents/close",
            token_kind="admin",
            json_body=payload
        )


if __name__ == '__main__':
    pass
