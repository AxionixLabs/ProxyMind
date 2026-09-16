# -*- coding: utf-8 -*-

from agent.domain.mcp_oauth import McpOAuthStorageError


class MemoryVault:
    """为凭据、授权和 CLI 测试注入独立机密存储及确定性故障。"""

    def __init__(self) -> None:
        """初始化无外部副作用的记录和故障开关。"""
        self.values: dict[str, str] = {}
        self.writes = 0
        self.fail_write: int | None = None
        self.fail_delete = False
        self.fail_read = False

    def read(self, key: str) -> str | None:
        """返回本例记录或模拟存储不可用。"""
        if self.fail_read:
            raise McpOAuthStorageError("storage_unavailable")
        return self.values.get(key)

    def write_new(self, key: str, value: str) -> None:
        """按写入序号模拟失败，并拒绝意外覆盖。"""
        self.writes += 1
        if self.fail_write == self.writes:
            raise McpOAuthStorageError("storage_unavailable")
        assert key not in self.values
        self.values[key] = value

    def delete(self, key: str) -> None:
        """幂等清理本例记录，保留可注入的删除失败。"""
        if self.fail_delete:
            raise McpOAuthStorageError("storage_unavailable")
        if key in self.values:
            del self.values[key]
