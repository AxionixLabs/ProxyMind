# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing


class EnhanceReporter(typing.Protocol):
    """描述结果增强过程需要的展示和状态能力。"""

    async def display(self, text: str) -> None:
        """展示增强过程产生的文本。"""
        ...

    async def begin_status(self) -> None:
        """启动增强过程状态。"""
        ...

    async def end_status(self) -> None:
        """结束增强过程状态。"""
        ...


if __name__ == '__main__':
    pass
