# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import time
import typing

import anyio
import httpx
from dataclasses import replace

from agent.domain.mcp_oauth import (
    McpMetadataClient,
    McpOAuthBinding,
    McpOAuthCredentialSnapshot,
    McpOAuthError,
    McpOAuthStorageError,
    McpRegisteredClient,
)
from agent.ports.mcp_credentials import McpCredentialStore
from infrastructure.mcp.oauth_adapter import McpOAuthRefreshAdapter


class McpOAuthRuntimeAuth(httpx.Auth):
    """单连接的非交互认证适配器；请求边界重读凭据，轮换事务收束后才允许关闭。

    所有请求继续使用原 SDK 会话。一次认证只发送一次业务请求，401/403 不产生重放。
    凭据锁与保存由注入存储拥有；刷新网络及等待任务由本适配器拥有并在请求内收束。
    """

    def __init__(
        self, binding: McpOAuthBinding, store: McpCredentialStore,
        *, failed: typing.Callable[[McpOAuthError | McpOAuthStorageError], None],
        refresher: McpOAuthRefreshAdapter | None = None,
        clock: typing.Callable[[], float] = time.time,
    ) -> None:
        """冻结本连接的授权边界与失败通知，不缓存 access token。"""
        self._binding = binding
        self._store = store
        self._failed = failed
        self._refresher = refresher or McpOAuthRefreshAdapter(clock=clock)
        self._clock = clock
        self._used_credentials = False
        self._failure: McpOAuthError | McpOAuthStorageError | None = None

    def _check_binding(self, snapshot: McpOAuthCredentialSnapshot) -> None:
        """拒绝配置变更后借用原客户端授权；有效范围由已批准凭据及远端裁决。"""
        selected = self._binding.registration
        if snapshot.target != self._binding.target or snapshot.client.registration != selected.kind:
            raise McpOAuthError("reauthorization_required")
        if isinstance(selected, McpRegisteredClient) and snapshot.client.client_id != selected.client_id:
            raise McpOAuthError("reauthorization_required")
        if isinstance(selected, McpMetadataClient) and snapshot.client.client_id != selected.metadata_url:
            raise McpOAuthError("reauthorization_required")
        if snapshot.recovery is not None:
            raise McpOAuthError(snapshot.recovery)
        token = snapshot.token
        if token is None:
            raise McpOAuthError("login_required")

    async def _refresh_locked(self, consuming: asyncio.Event) -> McpOAuthCredentialSnapshot:
        """在统一跨进程锁内重读、标记消费、刷新并保存，取消不能释放半完成轮换。"""
        async with self._store.transaction(self._binding.target) as transaction:
            snapshot = transaction.record.snapshot
            if snapshot is None:
                raise McpOAuthError("login_required")
            self._check_binding(snapshot)
            previous = snapshot.token
            if previous is None:
                raise McpOAuthError("login_required")
            if previous.expires_at is None or previous.expires_at > self._clock():
                return snapshot
            if previous.refresh_token is None:
                raise McpOAuthError("reauthorization_required")
            consuming.set()
            pending = await transaction.save(replace(snapshot, token=None, recovery="refresh_uncertain"))
            try:
                async with asyncio.timeout(5):
                    token = await self._refresher.refresh(snapshot)
            except TimeoutError:
                raise McpOAuthError("refresh_uncertain") from None
            except McpOAuthError as error:
                if error.code in ("reauthorization_required", "insufficient_scope"):
                    await transaction.save(replace(pending, recovery="reauthorization_required"))
                raise
            return await transaction.save(replace(pending, token=token, recovery=None))

    async def _refresh(self) -> McpOAuthCredentialSnapshot:
        """取锁可取消；开始消费后等待有界刷新及提交结束，再传播调用方取消。"""
        consuming = asyncio.Event()
        work = asyncio.create_task(self._refresh_locked(consuming), name="MCP OAuth credential refresh")
        try:
            return await asyncio.shield(work)
        except asyncio.CancelledError:
            if not consuming.is_set():
                work.cancel()
            with anyio.CancelScope(shield=True):
                while not work.done():
                    try:
                        await asyncio.shield(work)
                    except asyncio.CancelledError:
                        if not consuming.is_set():
                            work.cancel()
                    except (McpOAuthError, McpOAuthStorageError):
                        break
                if not work.cancelled():
                    work.exception()
            raise

    async def _credentials(self) -> McpOAuthCredentialSnapshot | None:
        """每次业务请求采用最新版本，空存储允许匿名探测，退出后不降级匿名。"""
        record = await self._store.read(self._binding.target, require_available=False)
        await anyio.lowlevel.checkpoint()
        snapshot = record.snapshot
        if snapshot is None:
            if self._used_credentials:
                raise McpOAuthError("login_required")
            return None
        self._check_binding(snapshot)
        self._used_credentials = True
        token = snapshot.token
        if token is not None and token.expires_at is not None and token.expires_at <= self._clock():
            return await self._refresh()
        return snapshot

    async def _invalidate(self, snapshot: McpOAuthCredentialSnapshot) -> None:
        """只失效被远端拒绝的当前版本，不覆盖并发登录或刷新提交。"""
        async with self._store.transaction(self._binding.target) as transaction:
            if transaction.record.generation == snapshot.generation:
                await transaction.save(replace(snapshot, token=None, recovery="reauthorization_required"))

    async def async_auth_flow(self, request: httpx.Request) -> typing.AsyncGenerator[httpx.Request, httpx.Response]:
        """为原请求附加一次授权；错误只传播安全代码，不追随重定向或重放请求。"""
        try:
            if self._failure is not None:
                raise self._failure
            if request.url != httpx.URL(self._binding.target.server_url):
                raise McpOAuthError("configuration_conflict")
            snapshot = await self._credentials()
            if snapshot is not None and snapshot.token is not None:
                request.headers["Authorization"] = f"Bearer {snapshot.token.access_token}"
            response = yield request
            if response.status_code in (401, 403):
                await response.aclose()
                if snapshot is not None:
                    await self._invalidate(snapshot)
                raise McpOAuthError("insufficient_scope" if response.status_code == 403 else "login_required")
            if 300 <= response.status_code < 400:
                await response.aclose()
                raise McpOAuthError("invalid_response")
        except (McpOAuthError, McpOAuthStorageError) as error:
            self._failure = error
            self._failed(error)
            raise


if __name__ == '__main__':
    pass
