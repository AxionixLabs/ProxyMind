# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio


def task_interrupt_active() -> bool:
    """返回当前异步任务是否已收到中断请求。"""
    task = asyncio.current_task()
    if task is None:
        return False

    cancelling = getattr(task, "cancelling", None)
    if callable(cancelling):
        return bool(cancelling())
    return task.cancelled()


if __name__ == '__main__':
    pass
