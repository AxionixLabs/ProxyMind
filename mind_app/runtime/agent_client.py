# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import socket
import typing
import httpx
import websockets
from urllib.parse import urlencode
from websockets.asyncio.client import ClientConnection
from .agent_protocol import DEFAULT_SHARED_SECRET, build_envelope, ensure_ws_base


class AgentClient(object):
    """HTTP + WS resident client for the /agents protocol."""

    def __init__(
        self,
        *,
        base_url: str,
        client_token: str = DEFAULT_SHARED_SECRET,
        admin_token: str = DEFAULT_SHARED_SECRET,
        timeout_sec: float = 15.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.client_token = client_token
        self.admin_token = admin_token
        self.timeout_sec = timeout_sec

    async def _request(
        self,
        method: str,
        path: str,
        *,
        token_kind: typing.Literal["client", "admin"],
        json_body: typing.Any = None,
        params: dict[str, typing.Any] | None = None,
    ) -> dict[str, typing.Any]:
        header_name = "X-Agent-Token" if token_kind == "client" else "X-Agent-Admin-Token"
        token_value = self.client_token if token_kind == "client" else self.admin_token

        async with httpx.AsyncClient(timeout=self.timeout_sec) as http:
            response = await http.request(
                method,
                f"{self.base_url}{path}",
                headers={
                    header_name: token_value,
                    "Content-Type": "application/json",
                },
                json=json_body,
                params=params,
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
        capabilities: dict[str, typing.Any] | None = None,
    ) -> dict[str, typing.Any]:
        payload = {
            "device_id": device_id,
            "agent_id": agent_id,
            "client_version": client_version,
            "platform": platform,
            "arch": arch,
            "hostname": hostname or socket.gethostname(),
            "capabilities": capabilities or {
                "tool_call": True,
                "event_push": True,
            },
        }
        return await self._request("POST", "/agents/open", token_kind="client", json_body=payload)

    async def resume_session(
        self,
        *,
        session_id: str,
        resume_token: str,
        last_acked_seq: int,
        device_id: str | None = None,
        agent_id: str | None = None,
    ) -> dict[str, typing.Any]:
        payload: dict[str, typing.Any] = {
            "session_id": session_id,
            "resume_token": resume_token,
            "last_acked_seq": last_acked_seq,
        }
        if device_id:
            payload["device_id"] = device_id
        if agent_id:
            payload["agent_id"] = agent_id
        return await self._request("POST", "/agents/resume", token_kind="client", json_body=payload)

    @staticmethod
    def unwrap_data(payload: dict[str, typing.Any]) -> dict[str, typing.Any]:
        """Return the protocol body when the server wraps fields in a top-level data object."""
        data = payload.get("data")
        if isinstance(data, dict):
            return data
        return payload

    def build_ws_url(self, *, session_id: str, ws_token: str, ws_base_url: str | None = None) -> str:
        query = urlencode({"session_id": session_id, "ws_token": ws_token})
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
        ws_base_url: str | None = None,
    ) -> ClientConnection:
        return await websockets.connect(
            self.build_ws_url(session_id=session_id, ws_token=ws_token, ws_base_url=ws_base_url),
            open_timeout=self.timeout_sec,
            close_timeout=self.timeout_sec,
            ping_interval=None,
        )

    @staticmethod
    async def send_json(connection: ClientConnection, envelope: dict[str, typing.Any]) -> None:
        await connection.send(json.dumps(envelope, ensure_ascii=False))

    @staticmethod
    async def recv_json(connection: ClientConnection) -> dict[str, typing.Any]:
        raw = await connection.recv()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return typing.cast(dict[str, typing.Any], json.loads(raw))

    async def send_hello(
        self,
        connection: ClientConnection,
        *,
        session_id: str,
        device_id: str,
        client_version: str,
    ) -> None:
        await self.send_json(
            connection,
            build_envelope(
                "hello",
                session_id,
                payload={
                    "client_version": client_version,
                    "device_id": device_id,
                },
            ),
        )

    async def send_resume(
        self,
        connection: ClientConnection,
        *,
        session_id: str,
        last_acked_seq: int,
    ) -> None:
        await self.send_json(
            connection,
            build_envelope(
                "resume",
                session_id,
                payload={"last_acked_seq": last_acked_seq},
            ),
        )

    async def send_pong(self, connection: ClientConnection, *, session_id: str) -> None:
        await self.send_json(connection, build_envelope("pong", session_id))

    async def send_ack(
        self,
        connection: ClientConnection,
        *,
        session_id: str,
        acked_message_id: str,
        status: str = "received",
    ) -> None:
        await self.send_json(
            connection,
            build_envelope(
                "ack",
                session_id,
                payload={
                    "acked_message_id": acked_message_id,
                    "status": status,
                },
            ),
        )

    async def send_tool_result(
        self,
        connection: ClientConnection,
        *,
        session_id: str,
        cid: str,
        sid: str,
        call_id: str,
        name: str,
        ok: bool = True,
        result: dict[str, typing.Any] | None = None,
        error: dict[str, typing.Any] | None = None,
    ) -> None:
        payload: dict[str, typing.Any] = {
            "call_id": call_id,
            "name": name,
            "ok": ok,
        }
        if ok:
            payload["result"] = result or {"debug": True}
        else:
            payload["error"] = error or {"message": "resident tool call failed"}

        await self.send_json(
            connection,
            build_envelope(
                "tool.result",
                session_id,
                cid=cid,
                sid=sid,
                payload=payload,
            ),
        )

    async def send_event(
        self,
        connection: ClientConnection,
        *,
        session_id: str,
        cid: str,
        sid: str,
        mode: str,
        event: dict[str, typing.Any],
    ) -> None:
        await self.send_json(
            connection,
            build_envelope(
                "event.push",
                session_id,
                cid=cid,
                sid=sid,
                payload={
                    "mode": mode,
                    "event": event,
                },
            ),
        )
