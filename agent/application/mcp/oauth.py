# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio

from agent.domain.mcp_oauth import (
    McpOAuthCredentialView,
    McpOAuthError,
    McpOAuthLoginRequest,
    McpOAuthLoginResult,
    McpOAuthLogoutResult,
    McpOAuthTarget,
)
from agent.ports.mcp_credentials import McpCredentialStore
from agent.ports.mcp_oauth import (
    McpOAuthAuthorizer,
    McpOAuthPresenter,
)


class McpOAuthService:
    """协调显式登录与本地凭据用例，不持有连接、浏览器或循环资源。"""

    def __init__(self, store: McpCredentialStore, authorizer: McpOAuthAuthorizer) -> None:
        """接收唯一凭据存储及授权端口。"""
        self._store = store
        self._authorizer = authorizer

    async def login(self, request: McpOAuthLoginRequest, presenter: McpOAuthPresenter) -> McpOAuthLoginResult:
        """授权期间释放凭据锁，提交时用原版本拒绝迟到结果。"""
        try:
            async with asyncio.timeout(request.timeout_sec):
                record = await self._store.read(request.target)
                snapshot = await self._authorizer.authorize(request, record.generation, presenter)
                if snapshot.token is None:
                    raise McpOAuthError("invalid_response")
                async with self._store.transaction(request.target) as transaction:
                    saved = await transaction.save(snapshot)
                return McpOAuthLoginResult(request.target, saved.token.expires_at if saved.token is not None else None)
        except TimeoutError:
            raise McpOAuthError("timeout") from None

    async def logout(self, target: McpOAuthTarget) -> McpOAuthLogoutResult:
        """删除当前目标的本地凭据，不修改配置或声称撤销远端授权。"""
        return await self._store.delete(target)

    async def view(self, target: McpOAuthTarget) -> McpOAuthCredentialView:
        """投影本地凭据状态，不发起发现或远端认证。"""
        return await self._store.view(target)


if __name__ == '__main__':
    pass
