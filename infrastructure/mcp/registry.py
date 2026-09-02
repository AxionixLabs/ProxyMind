# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from infrastructure.config.session import ConfigSession
from .settings import McpConfigError


class McpServerRegistry(object):
    """通过统一配置会话管理用户级 MCP 服务。"""

    def __init__(self, session: ConfigSession) -> None:
        self.session = session

    def list(self) -> tuple[tuple[str, dict[str, typing.Any]], ...]:
        """返回有效配置中按名称排序的服务。"""
        servers = self._effective_servers()

        return tuple(
            (name, dict(value))
            for name, value in sorted(servers.items())
        )

    def get(self, name: str) -> dict[str, typing.Any]:
        """返回有效配置中的指定服务。"""
        key = self._name(name)
        value = self._effective_servers().get(key)

        if not isinstance(value, dict):
            raise McpConfigError(f"MCP server not found: {key}")
        return dict(value)

    def add(self, name: str, value: dict[str, typing.Any]) -> None:
        """向用户配置添加一个服务。"""
        key = self._name(name)
        if key in self._effective_servers():
            raise McpConfigError(f"MCP server already exists: {key}")
        self.session.update_user({("mcp_servers", key): dict(value)})

    def remove(self, name: str) -> dict[str, typing.Any]:
        """从用户配置删除并返回指定服务。"""
        key = self._name(name)
        value = self._user_servers().get(key)

        if not isinstance(value, dict):
            raise McpConfigError(
                f"MCP server is not defined in user config: {key}"
            )

        self.session.delete_user((("mcp_servers", key),))

        return dict(value)

    def set_enabled(self, name: str, enabled: bool) -> None:
        """修改用户配置中指定服务的启用状态。"""
        key = self._name(name)
        if key not in self._user_servers():
            raise McpConfigError(
                f"MCP server is not defined in user config: {key}"
            )
        self.session.update_user({
            ("mcp_servers", key, "enabled"): bool(enabled),
        })

    def _effective_servers(self) -> dict[str, dict[str, typing.Any]]:
        """返回当前分层配置中的有效服务映射。"""
        value = self.session.load().get("mcp_servers")
        if not isinstance(value, dict):
            return {}

        return {
            str(name): dict(server)
            for name, server in value.items()
            if isinstance(server, dict)
        }

    def _user_servers(self) -> dict[str, dict[str, typing.Any]]:
        """返回用户配置中直接定义的服务映射。"""
        self.session.resolve()

        raw = self.session.store.read_raw()
        value = raw.get("mcp_servers")

        if not isinstance(value, dict):
            return {}

        return {
            str(name): dict(server)
            for name, server in value.items()
            if isinstance(server, dict)
        }

    @staticmethod
    def _name(value: str) -> str:
        """校验服务名称。"""
        name = str(value or "").strip()
        if not name:
            raise McpConfigError("MCP server name is empty")
        return name


if __name__ == "__main__":
    pass
