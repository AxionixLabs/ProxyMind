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


class McpOAuthCallbackInput(typing.Protocol):
    """由终端前端实现一次隐藏输入；授权适配器负责取消，输入实现负责恢复终端。

    返回完整回调 URL 供协议边界校验，不记录、回显或导航输入。
    长度不得超过 max_bytes；成功、失败和取消都必须停止读取并释放终端状态。
    """

    async def read_callback(self, *, max_bytes: int) -> str:
        """有界读取一次输入，用户中断传播取消，输入关闭报告固定错误。"""
        ...


class McpOAuthAuthorizer(typing.Protocol):
    """由基础设施实现授权协议及短期资源 owner；返回已验证快照，不自行保存。

    每次调用创建并关闭 HTTP 客户端、本机监听和连接任务；失败和取消必须收束这些资源。
    客户端注册、发现和远端返回值在此边界校验，SDK 对象不得向外传播。
    """

    async def authorize(
        self, request: McpOAuthLoginRequest, generation: int, presenter: McpOAuthPresenter,
        *, callback_input: McpOAuthCallbackInput | None = None,
    ) -> McpOAuthCredentialSnapshot:
        """完成显式授权；提供输入时不启动浏览器，按原版本返回尚未持久化的凭据。"""
        ...


if __name__ == '__main__':
    pass
