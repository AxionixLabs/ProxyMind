# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
from urllib.parse import urlsplit

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, field_validator

from ..page import render_page

agent_router = APIRouter(tags=["Agent"])


class ConnectionPayload(BaseModel):
    """定义本机订阅进程发布的连接资料。"""

    session_id: str = ""
    credential: str | None = None
    web_url: str | None = None
    state: typing.Literal["idle", "connecting", "online", "reconnecting", "stopped", "error"] = "idle"
    model_config = ConfigDict(extra="forbid")

    @field_validator("web_url")
    @classmethod
    def validate_web_url(cls, value: str | None) -> str | None:
        """仅接受无内嵌凭据的 HTTP 浏览器地址。"""
        if not value:
            return None
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("invalid web address")
        return value


class ConnectionData(ConnectionPayload):
    """携带可供页面判断就绪和过期状态的资料。"""

    has_credential: bool = False
    updated_at_ms: int = 0


class ConnectionResponse(BaseModel):
    """封装本地连接资料响应。"""

    ok: bool = True
    data: ConnectionData


@agent_router.get("/agent", include_in_schema=False)
async def api_agent_page() -> Response:
    """返回本地连接引导页面。"""
    return render_page("agent.html")


@agent_router.get("/api/agent", include_in_schema=False)
async def api_agent_load(request: Request, response: Response) -> ConnectionResponse:
    """读取当前连接资料并禁止凭据响应缓存。"""
    response.headers["Cache-Control"] = "no-store"
    data = request.app.state.agent_access
    return ConnectionResponse(data=data if isinstance(data, ConnectionData) else ConnectionData())


@agent_router.put("/api/agent", include_in_schema=False)
async def api_agent_save(payload: ConnectionPayload, request: Request, response: Response) -> ConnectionResponse:
    """保存订阅进程发布的连接状态和凭据。"""
    data = ConnectionData(**payload.model_dump(), has_credential=bool(payload.credential), updated_at_ms=int(time.time() * 1000))
    request.app.state.agent_access = data
    response.headers["Cache-Control"] = "no-store"
    return ConnectionResponse(data=data)


if __name__ == '__main__':
    pass
