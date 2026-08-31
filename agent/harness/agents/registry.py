# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
from collections.abc import (
    Awaitable,
    Callable
)
from .control import (
    AgentControl,
    AgentStateError,
)

__all__ = (
    "AgentControlFactory",
    "AgentControlRegistry",
)

AgentControlFactory = Callable[[str, bool], Awaitable[AgentControl]]


class AgentControlRegistry:
    """管理根会话执行树的注册、恢复和关闭生命周期。"""

    def __init__(
        self,
        factory: AgentControlFactory,
        *,
        enabled: bool = True,
    ) -> None:
        """创建由显式工厂负责恢复或构造 control 的注册表。"""
        if not callable(factory):
            raise TypeError("agent control factory is required")
        if not isinstance(enabled, bool):
            raise TypeError("agent control registry enabled state must be a boolean")

        self._factory = factory
        self._enabled = enabled
        self._lock = asyncio.Lock()
        self._controls: dict[str, AgentControl] = {}
        self._shutdown = False

    @property
    def enabled(self) -> bool:
        """返回注册表是否允许取得执行树。"""
        return self._enabled

    async def get(
        self,
        root_session_id: str,
        *,
        create_empty: bool = True,
    ) -> AgentControl:
        """返回根会话 control，缺失时按策略调用恢复工厂。"""
        normalized = _normalize_root_session_id(root_session_id)

        async with self._lock:
            self._require_available()
            control = self._controls.get(normalized)
            if control is None:
                control = await self._factory(normalized, create_empty)
                self._controls[normalized] = control
            return control

    async def remove(self, root_session_id: str) -> AgentControl | None:
        """移除并返回根会话 control；关闭后的注册表不再暴露旧实例。"""
        normalized = _normalize_root_session_id(root_session_id)
        async with self._lock:
            return self._controls.pop(normalized, None)

    async def shutdown(self) -> tuple[AgentControl, ...]:
        """原子标记关闭并移交全部 control 给调用方收尾。"""
        async with self._lock:
            if self._shutdown:
                return ()
            self._shutdown = True
            controls = tuple(self._controls.values())
            self._controls = {}
            return controls

    def _require_available(self) -> None:
        """确认注册表仍可创建或读取执行树。"""
        if self._shutdown:
            raise AgentStateError("subagent runtime is shut down")
        if not self._enabled:
            raise AgentStateError("subagent runtime is disabled")


def _normalize_root_session_id(value: str) -> str:
    """返回非空的根会话标识。"""
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError("root session id is required")
    return normalized


if __name__ == '__main__':
    pass
