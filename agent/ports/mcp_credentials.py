# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from contextlib import AbstractAsyncContextManager

from agent.domain.mcp_oauth import (
    McpOAuthCredentialRecord,
    McpOAuthCredentialSnapshot,
    McpOAuthCredentialView,
    McpOAuthLogoutResult,
    McpOAuthTarget,
)


class McpCredentialTransaction(typing.Protocol):
    """由存储实现持有单目标跨进程锁；仅在进入上下文的任务内使用并按期退出。

    网络刷新必须在此范围内重读和消费令牌。save 是独立的持久提交，退出上下文不撤销它；
    调用方负责网络期限，取消后仍须提交的轮换结果由认证用例保护其保存范围。
    """

    @property
    def record(self) -> McpOAuthCredentialRecord:
        """返回加锁后恢复的最新记录，包含无令牌的版本墓碑。"""
        ...

    async def save(self, snapshot: McpOAuthCredentialSnapshot) -> McpOAuthCredentialSnapshot:
        """仅接受当前目标和 generation，成功后返回递增版本的持久记录。"""
        ...

    async def delete(self) -> McpOAuthLogoutResult:
        """删除本地凭据并持久化递增版本，清理失败不得报告成功。"""
        ...


class McpCredentialStore(typing.Protocol):
    """本地 OAuth 凭据的唯一访问端口，由基础设施实现，组合边界注入路径与后端。

    不持有 MCP 连接或打开浏览器。每次事务拥有并关闭自己的锁、线程工作和数据库连接。
    前端只消费 view；snapshot 仅供认证用例使用。
    """

    def transaction(self, target: McpOAuthTarget) -> AbstractAsyncContextManager[McpCredentialTransaction]:
        """取得有期限的跨进程独占范围，进入时恢复并清理未提交记录。"""
        ...

    async def read(self, target: McpOAuthTarget, *, require_available: bool = True) -> McpOAuthCredentialRecord:
        """恢复最新凭据；匿名运行时可不探测空记录的系统库，已有机密仍须严格读取。"""
        ...

    async def delete(self, target: McpOAuthTarget) -> McpOAuthLogoutResult:
        """清除当前目标，包括无法解码的机密；保留版本墓碑并报告清理失败。"""
        ...

    async def view(self, target: McpOAuthTarget) -> McpOAuthCredentialView:
        """读取可展示的本地状态，错误只投影固定代码。"""
        ...


if __name__ == '__main__':
    pass
