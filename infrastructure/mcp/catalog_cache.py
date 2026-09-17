# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import hashlib
import json
import math
import time
import typing
from collections import OrderedDict
from dataclasses import (
    asdict,
    dataclass,
)
from pathlib import Path

from mcp import types as mcp_types
from mcp.client.stdio import get_default_environment

from infrastructure.mcp.settings import NormalizedMcpServer

CATALOG_SNAPSHOT_MAX_BYTES = 16 * 1024 * 1024


def configuration_identity(server: NormalizedMcpServer) -> str:
    """对已解析连接、过滤和审批配置取摘要，不保存或输出认证头正文。"""
    values = {key: value for key, value in server.items() if key not in ("authorization", "oauth_binding", "enabled")}
    binding = server.get("oauth_binding")
    values["oauth_binding"] = asdict(binding) if binding is not None else None
    if server.get("transport") == "stdio":
        values["env"] = {**get_default_environment(), **server.get("env", {})}
    encoded = json.dumps(values, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


@dataclass(frozen=True, slots=True)
class CatalogIdentity:
    """限定一个 runtime 的派生目录；配置、工作区、服务和凭据代际必须全部匹配。"""

    workspace: Path
    config_key: str
    configuration: str
    generation: int | None


@dataclass(frozen=True, slots=True)
class CatalogSnapshot:
    """保存不可变 SDK 序列化值和内容版本，不持有会话、权限或可变 SDK 对象。"""

    revision: str
    entries: tuple[tuple[str, str], ...]
    size: int

    @classmethod
    def capture(cls, tools: typing.Mapping[str, mcp_types.Tool]) -> "CatalogSnapshot":
        """将完整定义及审批元数据纳入版本，空目录同样有明确版本。"""
        entries: list[tuple[str, str]] = []
        size = 0
        digest = hashlib.sha256()
        for name, tool in sorted(tools.items()):
            value = json.dumps(tool.model_dump(mode="json", by_alias=True), sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
            for part in (name.encode("utf-8"), value.encode("ascii")):
                digest.update(len(part).to_bytes(8, "big"))
                digest.update(part)
                size += len(part)
            if size <= CATALOG_SNAPSHOT_MAX_BYTES:
                entries.append((name, value))
            else:
                entries.clear()
        return cls(digest.hexdigest(), tuple(entries), size)

    def preview(self) -> dict[str, mcp_types.Tool]:
        """恢复独立目录，缓存不能提供免审批注解或创建可记忆授权。"""
        tools = {}
        for name, value in self.entries:
            tool = mcp_types.Tool.model_validate_json(value)
            meta = dict(tool.meta or {})
            meta.update(approval_mode="prompt", approval_allow_session=False, approval_allow_persistent=False)
            tools[name] = tool.model_copy(update={"annotations": None, "meta": meta})
        return tools


@dataclass(frozen=True, slots=True)
class _Entry:
    """记录最后成功发布时刻，读取不延长有效期。"""

    published_at: float
    snapshot: CatalogSnapshot


class ToolCatalogCache:
    """由单个 runtime 拥有的有界派生缓存，没有任务、磁盘或连接生命周期。

    只有已通过 required 批次判定的实时连接可发布；失败、身份变化和退休由 owner 失效。
    返回的目录不能代表连接就绪，调用仍须验证该次连接及完整目录版本。
    """

    def __init__(self, *, capacity: int = 32, max_bytes: int = 16 * 1024 * 1024, ttl_sec: float = 1800, clock: typing.Callable[[], float] = time.monotonic) -> None:
        """冻结容量、总字节预算和 TTL，时钟由测试或组合方注入。"""
        if capacity <= 0 or max_bytes <= 0 or not math.isfinite(ttl_sec) or ttl_sec <= 0:
            raise ValueError("MCP catalog cache limits must be positive")
        self._capacity = capacity
        self._max_bytes = max_bytes
        self._ttl = ttl_sec
        self._clock = clock
        self._entries: OrderedDict[CatalogIdentity, _Entry] = OrderedDict()

    def _expire(self) -> None:
        """每次访问清除过期项，不创建计时后台任务。"""
        now = self._clock()
        for identity, entry in tuple(self._entries.items()):
            if now - entry.published_at >= self._ttl:
                del self._entries[identity]

    def get(self, identity: CatalogIdentity) -> CatalogSnapshot | None:
        """命中时只更新 LRU 顺序，绝不刷新发布时间。"""
        self._expire()
        entry = self._entries.get(identity)
        if entry is None:
            return None
        self._entries.move_to_end(identity)
        return entry.snapshot

    def publish(self, identity: CatalogIdentity, snapshot: CatalogSnapshot) -> None:
        """以当前身份替换同服务旧项，超大目录仅供实时连接使用。"""
        self._expire()
        self.invalidate(identity.config_key)
        if snapshot.size > min(self._max_bytes, CATALOG_SNAPSHOT_MAX_BYTES):
            return
        self._entries[identity] = _Entry(self._clock(), snapshot)
        while len(self._entries) > self._capacity or sum(item.snapshot.size for item in self._entries.values()) > self._max_bytes:
            self._entries.popitem(last=False)

    def invalidate(self, key: str) -> None:
        """清除该服务全部身份，旧结果不得在失败后继续提供预览。"""
        for identity in tuple(self._entries):
            if identity.config_key == key:
                del self._entries[identity]

    def clear(self) -> None:
        """在 runtime 退休时丢弃全部派生数据。"""
        self._entries.clear()


if __name__ == '__main__':
    pass
