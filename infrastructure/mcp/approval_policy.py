# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio

from agent.domain.approvals import McpToolDescriptor
from agent.ports import McpPersistentApprovalStore
from infrastructure.config.session import ConfigSession
from infrastructure.mcp.settings import McpConfigError


class ConfigMcpPersistentApprovalStore(McpPersistentApprovalStore):
    """通过原子 ConfigStore 更新持久允许一个 MCP 工具。"""

    def __init__(self, config: ConfigSession) -> None:
        """绑定当前分层配置会话。"""
        self._config = config

    async def approve_tool(self, descriptor: McpToolDescriptor) -> None:
        """写入工具级 approve，并验证结果没有被 Profile 或 CLI 遮蔽。"""
        await asyncio.to_thread(self._approve_tool, descriptor)

    def _approve_tool(self, descriptor: McpToolDescriptor) -> None:
        """同步完成配置存在性检查和原子更新。"""
        server_key = str(descriptor.config_server_key or "").strip()
        if not server_key:
            raise McpConfigError("MCP config server identity is unavailable")
        servers = self._config.load().get("mcp_servers")
        if not isinstance(servers, dict) or not isinstance(
            servers.get(server_key),
            dict,
        ):
            raise McpConfigError(f"MCP server not found: {server_key}")

        path = (
            "mcp_servers",
            server_key,
            "tools",
            descriptor.tool_name,
            "approval_mode",
        )
        self._config.update_user(
            {path: "approve"},
            ensure_effective={path: "approve"},
        )


if __name__ == '__main__':
    pass
