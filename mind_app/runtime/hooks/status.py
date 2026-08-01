# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
from mind_app.frontend import FrontendRuntime


class HookStatusCoordinator:
    """协调并发 Hook 命令使用的前端临时状态。"""

    def __init__(self, runtime: FrontendRuntime) -> None:
        self._runtime = runtime

        self._active: list[tuple[str, str]] = []

        self._lock: asyncio.Lock = asyncio.Lock()

    async def started(self, key: str, message: str) -> None:
        """登记状态消息并在首个任务开始时启动展示。"""
        normalized = str(message or "").strip()
        if not normalized:
            return None

        async with self._lock:
            should_start = not self._active
            self._active.append((key, normalized))
            if should_start:
                await self._runtime.begin_operation_status(self.snapshot)

    async def completed(self, key: str) -> None:
        """移除一个任务状态并在全部结束时清理展示。"""
        async with self._lock:
            for index, (active_key, _message) in enumerate(self._active):
                if active_key == key:
                    self._active.pop(index)
                    break
            if not self._active:
                await self._runtime.end_activity_status(
                    "operation",
                    settle=False,
                )

    def snapshot(self) -> dict[str, str]:
        """返回当前最近一个 Hook 命令的状态文本。"""
        summary = self._active[-1][1] if self._active else ""
        return {"summary": summary}


if __name__ == '__main__':
    pass
