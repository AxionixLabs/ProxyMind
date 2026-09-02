# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

__all__ = ("ProcessResourcePort",)


@typing.runtime_checkable
class ProcessResourcePort(typing.Protocol):
    """定义进程级共享资源的有序关闭契约。

    实现方必须串行化并发关闭；某一步失败后再次调用时从该步骤继续，不得重复关闭
    已经完成的前置资源。
    """

    async def close(self) -> None:
        """按依赖逆序关闭全部进程资源。"""
        ...


if __name__ == '__main__':
    pass
