# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from agent.domain.mcp_oauth import (
    McpOAuthCredentialSnapshot,
    McpOAuthLoginRequest,
)


class McpOAuthPresenter(typing.Protocol):
    """由 CLI 实现短期授权提示；URL 只输出给用户，不写入模型或观测事件。"""

    def authorization_url(self, url: str) -> None:
        """在启动浏览器之前显示并刷新完整授权地址。"""
        ...

    def browser_unavailable(self) -> None:
        """提示用户手动打开已显示的地址，不把浏览器失败视为授权成功。"""
        ...


class McpOAuthAuthorizer(typing.Protocol):
    """由基础设施实现授权协议及短期资源 owner；返回已验证快照，不自行保存。

    每次调用创建并关闭 HTTP 客户端、本机监听和连接任务；失败和取消必须收束这些资源。
    客户端注册、发现和远端返回值在此边界校验，SDK 对象不得向外传播。
    """

    async def authorize(
        self, request: McpOAuthLoginRequest, generation: int, presenter: McpOAuthPresenter,
    ) -> McpOAuthCredentialSnapshot:
        """完成一次显式授权，按传入版本构造尚未持久化的凭据。"""
        ...


if __name__ == '__main__':
    pass
