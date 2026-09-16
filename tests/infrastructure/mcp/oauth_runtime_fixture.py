# -*- coding: utf-8 -*-

import asyncio
import json
import time

import httpx
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qsl

from pydantic import JsonValue

from agent.domain.mcp_oauth import (
    McpDynamicClient,
    McpOAuthBinding,
    McpOAuthClientInfo,
    McpOAuthCredentialSnapshot,
    McpOAuthTarget,
    McpOAuthToken,
)
from infrastructure.mcp.oauth_adapter import McpOAuthRefreshAdapter
from infrastructure.mcp.oauth_credentials import SystemMcpCredentialStore
from infrastructure.mcp.oauth_runtime import McpOAuthRuntimeAuth
from tests.fakes.mcp_credentials import MemoryVault
from tests.infrastructure.mcp.oauth_fixture import (
    ISSUER_URL,
    ProbeRpcRequest,
    RESOURCE_URL,
    TOKEN_URL,
    TokenRequest,
)


class RuntimeOAuthFixture:
    """提供真实存储、可轮换令牌和协议 HTTP 边界，供认证与生产运行时测试共享。"""

    def __init__(self, root: Path) -> None:
        """创建独立凭据命名空间和受控服务事实。"""
        self.now = time.time()
        self.target = McpOAuthTarget(" raw server ", RESOURCE_URL)
        self.binding = McpOAuthBinding(self.target, McpDynamicClient())
        self.vault = MemoryVault()
        self.store = SystemMcpCredentialStore(config_root=root / "config", state_root=root / "state", vault=self.vault, clock=lambda: self.now)
        self.token = McpOAuthToken("access-1", "refresh-1", self.now + 3600, ("read",))
        self.access = "access-1"
        self.refresh = "refresh-1"
        self.issued = 1
        self.refresh_requests: list[TokenRequest] = []
        self.requests: list[httpx.Request] = []
        self.rpc_methods: list[str] = []
        self.refresh_entered = asyncio.Event()
        self.refresh_release = asyncio.Event()
        self.refresh_release.set()
        self.refresh_mode = "success"
        self.resource_status = 200
        self.anonymous = False
        self.calls = 0

    async def save(self, *, expired: bool = False) -> McpOAuthCredentialSnapshot:
        """创建当前测试服务对应的已登录记录，允许模拟冷启动时已经过期。"""
        async with self.store.transaction(self.target) as transaction:
            token = replace(self.token, expires_at=self.now - 1) if expired else self.token
            return await transaction.save(McpOAuthCredentialSnapshot(
                self.target, ISSUER_URL, RESOURCE_URL, TOKEN_URL,
                McpOAuthClientInfo("public-client", ("http://127.0.0.1:12345/callback",), "dynamic"),
                transaction.record.generation, token,
            ))

    def client(self) -> httpx.AsyncClient:
        """创建独立 HTTPX 客户端，由调用的适配器关闭。"""
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handle))

    def auth(self) -> McpOAuthRuntimeAuth:
        """创建不共享缓存的新连接认证实例。"""
        return McpOAuthRuntimeAuth(self.binding, self.store, failed=lambda error: None,
            refresher=McpOAuthRefreshAdapter(client_factory=self.client, clock=lambda: self.now), clock=lambda: self.now)

    async def handle(self, request: httpx.Request) -> httpx.Response:
        """在传输边界记录并处理请求，令牌错误不生成成功响应。"""
        self.requests.append(request)
        if str(request.url) == TOKEN_URL:
            form = TokenRequest.model_validate(dict(parse_qsl(request.content.decode("ascii"))))
            assert form.grant_type == "refresh_token" and form.resource == RESOURCE_URL
            self.refresh_requests.append(form)
            self.refresh_entered.set()
            await self.refresh_release.wait()
            if self.refresh_mode == "network":
                raise httpx.ReadError("private-error-with-token", request=request)
            if self.refresh_mode == "redirect":
                return httpx.Response(307, headers={"Location": "https://attacker.test/token"})
            if self.refresh_mode == "invalid_grant" or form.refresh_token != self.refresh:
                return httpx.Response(400, json={"error": "invalid_grant"})
            self.issued += 1
            self.access, self.refresh = f"access-{self.issued}", f"refresh-{self.issued}"
            payload: dict[str, JsonValue] = {
                "access_token": self.access, "refresh_token": self.refresh,
                "expires_in": 3600, "token_type": "Bearer", "scope": "read",
            }
            if self.refresh_mode == "scope":
                payload["scope"] = "admin"
            if self.refresh_mode == "invalid_token":
                payload["access_token"] = "private-token\r\ninjected-header"
            if self.refresh_mode == "no_rotation":
                del payload["refresh_token"]
                self.refresh = form.refresh_token
            return httpx.Response(200, json=payload)
        assert str(request.url) == RESOURCE_URL
        if self.resource_status != 200:
            return httpx.Response(self.resource_status, headers={"Location": "https://attacker.test/mcp"})
        if not self.anonymous and request.headers.get("Authorization") != f"Bearer {self.access}":
            return httpx.Response(401, headers={"WWW-Authenticate": 'Bearer error="invalid_token"'})
        if request.method != "POST":
            return httpx.Response(405)
        message = ProbeRpcRequest.model_validate_json(request.content)
        self.rpc_methods.append(message.method)
        if message.id is None:
            return httpx.Response(202)
        result: dict[str, JsonValue]
        if message.method == "initialize":
            result = {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}}, "serverInfo": {"name": "fixture", "version": "1"}}
        elif message.method == "tools/list":
            result = {"tools": [{"name": "read_fixture", "inputSchema": {"type": "object"}, "annotations": {"readOnlyHint": True}}]}
        else:
            assert message.method == "tools/call"
            self.calls += 1
            result = {"content": [{"type": "text", "text": "fixture result"}]}
        return httpx.Response(200, content=json.dumps({"jsonrpc": "2.0", "id": message.id, "result": result}), headers={"Content-Type": "application/json"})
