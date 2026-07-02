# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio


class AsyncRWLock:
    """异步读写锁；读锁可并发，写锁独占。"""

    def __init__(self) -> None:
        self._cond: asyncio.Condition = asyncio.Condition()

        self._readers: int   = 0
        self._writer: bool   = False
        self._w_writers: int = 0

    def read(self) -> "_ReadGuard":
        """创建读锁上下文。"""
        return _ReadGuard(self)

    def write(self) -> "_WriteGuard":
        """创建写锁上下文。"""
        return _WriteGuard(self)

    async def acquire_read(self) -> None:
        """获取读锁。"""
        async with self._cond:
            while self._writer or self._w_writers > 0:
                await self._cond.wait()
            self._readers += 1

    async def release_read(self) -> None:
        """释放读锁。"""
        async with self._cond:
            self._readers = max(0, self._readers - 1)
            if self._readers == 0:
                self._cond.notify_all()

    async def acquire_write(self) -> None:
        """获取写锁。"""
        async with self._cond:
            self._w_writers += 1
            try:
                while self._writer or self._readers > 0:
                    await self._cond.wait()
                self._writer = True
            finally:
                self._w_writers = max(0, self._w_writers - 1)

    async def release_write(self) -> None:
        """释放写锁。"""
        async with self._cond:
            self._writer = False
            self._cond.notify_all()


class _ReadGuard:
    """读锁上下文管理器。"""

    def __init__(self, lock: AsyncRWLock) -> None:
        self._lock = lock

    async def __aenter__(self) -> None:
        await self._lock.acquire_read()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self._lock.release_read()


class _WriteGuard:
    """写锁上下文管理器。"""

    def __init__(self, lock: AsyncRWLock) -> None:
        self._lock = lock

    async def __aenter__(self) -> None:
        await self._lock.acquire_write()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self._lock.release_write()


if __name__ == '__main__':
    pass
