# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import time
import typing

import anyio
import httpx
from dataclasses import replace

from agent.domain.mcp_authorization import (
    McpAuthorizationStatus,
    authorization_accepted,
    authorization_failed,
    authorization_from_credentials,
)
from agent.domain.mcp_oauth import (
    McpMetadataClient,
    McpOAuthBinding,
    McpOAuthCredentialSnapshot,
    McpOAuthCredentialRecord,
    McpOAuthError,
    McpOAuthStorageError,
    McpRegisteredClient,
    credential_view_from_record,
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
        observed: typing.Callable[[McpAuthorizationStatus], None],
        refresher: McpOAuthRefreshAdapter | None = None,
        clock: typing.Callable[[], float] = time.time,
    ) -> None:
        """冻结本连接的授权边界与失败通知，不缓存 access token。"""
        self._binding = binding
        self._store = store
        self._failed = failed
        self._observed = observed
        self._authorization = McpAuthorizationStatus()
        self._refresher = refresher or McpOAuthRefreshAdapter(clock=clock)
        self._clock = clock
        self._used_credentials = False
        self._failure: McpOAuthError | McpOAuthStorageError | None = None

    def _publish_authorization(self, status: McpAuthorizationStatus) -> None:
        """拒绝并发旧请求迟到的观察，凭据代际只在本连接身份内比较。"""
        current = self._authorization.generation
        if current is not None and status.generation is not None and status.generation < current:
            return
        self._authorization = status
        self._observed(status)

    def _observe_credentials(self, record: McpOAuthCredentialRecord) -> McpAuthorizationStatus:
        """只向所属连接发布无机密的凭据事实，不为展示增加存储访问。"""
        view = credential_view_from_record(self._binding.target, record, now=self._clock())
        previous = self._authorization if self._authorization.generation == record.generation else McpAuthorizationStatus()
        status = authorization_from_credentials(view, previous)
        self._publish_authorization(status)
        return status

    def _observe_snapshot(self, snapshot: McpOAuthCredentialSnapshot) -> McpAuthorizationStatus:
        """发布本次已完成的持久提交，避免刷新失败仍展示旧令牌。"""
        return self._observe_credentials(McpOAuthCredentialRecord(snapshot.generation, snapshot))

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
            self._observe_credentials(transaction.record)
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
            self._observe_snapshot(pending)
            try:
                async with asyncio.timeout(5):
                    token = await self._refresher.refresh(snapshot)
            except TimeoutError:
                raise McpOAuthError("refresh_uncertain") from None
            except McpOAuthError as error:
                if error.code in ("reauthorization_required", "insufficient_scope"):
                    self._observe_snapshot(await transaction.save(replace(pending, recovery="reauthorization_required")))
                raise
            saved = await transaction.save(replace(pending, token=token, recovery=None))
            self._observe_snapshot(saved)
            return saved

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

    async def _credentials(self) -> tuple[McpOAuthCredentialSnapshot | None, McpAuthorizationStatus]:
        """每次业务请求采用最新版本，空存储允许匿名探测，退出后不降级匿名。"""
        record = await self._store.read(self._binding.target, require_available=False)
        status = self._observe_credentials(record)
        await anyio.lowlevel.checkpoint()
        snapshot = record.snapshot
        if snapshot is None:
            if self._used_credentials:
                raise McpOAuthError("login_required")
            return None, status
        self._check_binding(snapshot)
        self._used_credentials = True
        token = snapshot.token
        if token is not None and token.expires_at is not None and token.expires_at <= self._clock():
            snapshot = await self._refresh()
            status = self._observe_snapshot(snapshot)
        return snapshot, status

    async def _invalidate(self, snapshot: McpOAuthCredentialSnapshot) -> McpAuthorizationStatus | None:
        """只失效被远端拒绝的当前版本，不覆盖并发登录或刷新提交。"""
        async with self._store.transaction(self._binding.target) as transaction:
            if transaction.record.generation == snapshot.generation:
                return self._observe_snapshot(await transaction.save(replace(snapshot, token=None, recovery="reauthorization_required")))
            self._observe_credentials(transaction.record)
            return None

    async def async_auth_flow(self, request: httpx.Request) -> typing.AsyncGenerator[httpx.Request, httpx.Response]:
        """为原请求附加一次授权；错误只传播安全代码，不追随重定向或重放请求。"""
        if self._failure is not None:
            raise self._failure
        request_status: McpAuthorizationStatus | None = None
        try:
            if request.url != httpx.URL(self._binding.target.server_url):
                raise McpOAuthError("configuration_conflict")
            snapshot, request_status = await self._credentials()
            if snapshot is not None and snapshot.token is not None:
                request.headers["Authorization"] = f"Bearer {snapshot.token.access_token}"
            response = yield request
            if response.status_code in (401, 403):
                await response.aclose()
                if snapshot is not None:
                    invalidated = await self._invalidate(snapshot)
                    if invalidated is not None:
                        request_status = invalidated
                raise McpOAuthError("insufficient_scope" if response.status_code == 403 else "login_required")
            if 300 <= response.status_code < 400:
                await response.aclose()
                raise McpOAuthError("invalid_response")
            if 200 <= response.status_code < 300:
                self._publish_authorization(authorization_accepted(request_status))
        except (McpOAuthError, McpOAuthStorageError) as error:
            self._publish_authorization(authorization_failed(request_status or self._authorization, error.code))
            self._failure = error
            self._failed(error)
            raise


if __name__ == '__main__':
    pass
