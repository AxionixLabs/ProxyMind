# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import httpx
import socket
import typing
import websockets
from urllib.parse import urlencode
from websockets.asyncio.client import ClientConnection
from .agent_protocol import (
    build_envelope, ensure_ws_base
)
from mind_nova import const


class AgentClient(object):
    """`/agents` 协议使用的 HTTP + WS 驻留客户端。"""

    def __init__(
        self,
        *,
        base_url: str,
        timeout_sec: float = 15.0
    ) -> None:
        """初始化驻留客户端的基础地址和超时时间。"""
        self.base_url = base_url.rstrip("/")
        self.timeout_sec = timeout_sec

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
        if token_kind == "client":
            header_name = "X-Agent-Token"
            token_value = const.AGENT_CLIENT_SECRET
        else:
            header_name = "X-Agent-Admin-Token"
            token_value = const.AGENT_ADMIN_SECRET

        async with httpx.AsyncClient(timeout=self.timeout_sec) as http:
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
        """调用 `/agents/open` 创建新的驻留会话。"""
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
        """调用 `/agents/resume` 恢复已存在的驻留会话。"""
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
        """建立驻留协议的 WebSocket 连接。"""
        return await websockets.connect(
            self.build_ws_url(session_id=session_id, ws_token=ws_token, ws_base_url=ws_base_url),
            open_timeout=self.timeout_sec,
            close_timeout=1.0,
            ping_interval=None
        )

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
                    "call_id"           : call_id,
                    "acked_message_id"  : acked_message_id,
                }
            )
        )


if __name__ == '__main__':
    pass
