# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio

from agent.domain.approvals import (
    ApprovalGrantKey,
    SessionGrant,
)


class InMemorySessionGrantStore:
    """保存只在当前进程和 Session 生命周期内有效的授权。"""

    def __init__(self) -> None:
        """创建空的 Session grant 索引。"""
        self._grants: dict[ApprovalGrantKey, SessionGrant] = {}
        self._lock = asyncio.Lock()

    async def find(self, key: ApprovalGrantKey) -> SessionGrant | None:
        """按精确 grant key 读取授权。"""
        async with self._lock:
            return self._grants.get(key)

    async def remember(self, grant: SessionGrant) -> None:
        """保存授权，并拒绝同一 key 的语义冲突。"""
        async with self._lock:
            current = self._grants.get(grant.key)
            if current is not None and current.decision != grant.decision:
                raise ValueError("session grant conflicts with existing decision")
            self._grants[grant.key] = grant

    async def clear(self, session_id: str) -> None:
        """清除指定 Session 的全部 grant。"""
        normalized = str(session_id or "").strip()
        if not normalized:
            raise ValueError("session_id is required")
        async with self._lock:
            for key in tuple(self._grants):
                if key.session_id == normalized:
                    del self._grants[key]


if __name__ == '__main__':
    pass
