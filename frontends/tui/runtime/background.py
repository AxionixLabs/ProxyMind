# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from collections.abc import (
    Iterator,
    Sequence
)
from dataclasses import dataclass
from frontends.tui.contracts.text import FragmentBlock
from ..core.activity import ActivityLease

BackgroundFailureHandler: typing.TypeAlias = typing.Callable[
    [BaseException],
    None,
]


@dataclass(frozen=True, slots=True)
class DeferredBlock(object):
    """保存等待安全边界提交的正文块和活动租约。"""
    block: FragmentBlock
    transcript_block: FragmentBlock
    activity_lease: ActivityLease | None = None


class DeferredBlockBuffer(Sequence[DeferredBlock]):
    """按完成顺序持有延迟正文，并以批次方式转移所有权。"""

    def __init__(self) -> None:
        self._blocks: list[DeferredBlock] = []

    def __len__(self) -> int:
        return len(self._blocks)

    def __getitem__(self, index: int) -> DeferredBlock:
        return self._blocks[index]

    def __iter__(self) -> Iterator[DeferredBlock]:
        return iter(self._blocks)

    def append(self, block: DeferredBlock) -> None:
        """把一项已完成正文加入等待队列。"""
        self._blocks.append(block)

    def drain(self) -> tuple[DeferredBlock, ...]:
        """转移并清空当前批次的全部延迟正文。"""
        blocks = tuple(self._blocks)
        self._blocks.clear()
        return blocks


class BackgroundTaskManager(object):
    """拥有 TUI 后台任务，负责会话去重、异常回报和关闭清理。"""

    def __init__(self, report_failure: BackgroundFailureHandler) -> None:
        self._report_failure = report_failure
        self._tasks: set[asyncio.Task[None]] = set()
        self._session_tasks: dict[str, asyncio.Task[None]] = {}

    def __bool__(self) -> bool:
        return bool(self._tasks)

    def __iter__(self) -> Iterator[asyncio.Task[None]]:
        return iter(tuple(self._tasks))

    def _task_done(self, task: asyncio.Task[None]) -> None:
        """回收完成任务及其会话索引，并上报非取消异常。"""
        self._tasks.discard(task)
        for session_id, session_task in tuple(self._session_tasks.items()):
            if session_task is task:
                self._session_tasks.pop(session_id, None)

        if task.cancelled():
            return None

        error = task.exception()
        if error is not None:
            self._report_failure(error)

    def start(
        self,
        coroutine: typing.Coroutine[typing.Any, typing.Any, None],
        *,
        name: str,
    ) -> asyncio.Task[None]:
        """创建并托管一项后台任务。"""
        task = asyncio.create_task(coroutine, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)
        return task

    def start_session(
        self,
        session_id: str,
        coroutine: typing.Coroutine[typing.Any, typing.Any, None],
    ) -> asyncio.Task[None]:
        """为标准化会话标识替换并启动唯一后台任务。"""
        sid = str(session_id or "").strip()
        self.cancel_session(sid)
        task = self.start(coroutine, name=f"process background {sid}")
        self._session_tasks[sid] = task
        return task

    def cancel_session(self, session_id: str) -> None:
        """取消指定会话仍在运行的后台任务。"""
        task = self._session_tasks.pop(
            str(session_id or "").strip(),
            None,
        )
        if task is not None and not task.done():
            task.cancel()

    async def close(self) -> None:
        """取消并等待当前管理器拥有的全部后台任务。"""
        tasks = tuple(self._tasks)
        self._tasks.clear()
        self._session_tasks.clear()

        for task in tasks:
            task.cancel()

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == '__main__':
    pass
