# -*- coding: utf-8 -*-

import asyncio

import httpx

from tests.infrastructure.mcp.oauth_fixture import OAuthService


async def visit_callback(url: str) -> int:
    """通过本机 TCP 访问回调，避免测试 HTTP 日志记录模拟授权码。"""
    parsed = httpx.URL(url)
    assert parsed.host == "127.0.0.1" and parsed.port is not None
    reader, writer = await asyncio.open_connection(parsed.host, parsed.port)
    try:
        writer.write(
            b"GET " + parsed.raw_path + b" HTTP/1.1\r\nHost: "
            + parsed.netloc + b"\r\nConnection: close\r\n\r\n"
        )
        await writer.drain()
        response = await reader.read()
        return int(response.split(b" ", 2)[1])
    finally:
        writer.close()
        await writer.wait_closed()


class OAuthBrowser:
    """模拟浏览器访问授权服务和真实 loopback 回调，不绕过 state 或 PKCE 校验。"""

    def __init__(self, service: OAuthService) -> None:
        """绑定独立授权服务与浏览器行为。"""
        self.service = service
        self.callback_url: str | None = None
        self.authorization_url: str | None = None
        self.mode = "success"
        self.result = True

    async def open(self, url: str) -> bool:
        """访问授权端点并按测试情形提交回调或取消。"""
        self.authorization_url = url
        async with self.service.client() as client:
            response = await client.get(url)
        assert response.status_code == 302
        callback = httpx.URL(response.headers["Location"])
        self.callback_url = str(callback)
        if self.mode == "cancel":
            raise asyncio.CancelledError
        if self.mode == "timeout":
            return self.result
        if self.mode == "denied":
            callback = callback.copy_remove_param("code").copy_add_param("error", "access_denied")
        elif self.mode == "wrong_state":
            callback = callback.copy_set_param("state", "wrong-state")
        elif self.mode == "wrong_issuer":
            callback = callback.copy_add_param("iss", "https://wrong.example/issuer")
        elif self.mode == "missing_code":
            callback = callback.copy_remove_param("code")
        status = await visit_callback(str(callback))
        assert status == (400 if self.mode in ("wrong_state", "wrong_issuer", "missing_code") else 200)
        return self.result


async def assert_callback_closed(url: str | None) -> None:
    """确认当前授权监听已退出，不再接受连接。"""
    if url is None:
        return
    parsed = httpx.URL(url)
    assert parsed.port is not None
    try:
        _, writer = await asyncio.open_connection(parsed.host, parsed.port)
    except OSError:
        return
    writer.close()
    await writer.wait_closed()
    raise AssertionError("OAuth callback listener is still open")
