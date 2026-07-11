# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing


class ToolDisplayResult(typing.Protocol):
    """描述工具结果展示层需要读取的字段。"""
    ok: bool
    text: str
    data: typing.Any
    cost_ms: int

    @property
    def fields(self) -> typing.Union[str, dict[str, typing.Any]]:
        """返回工具结果回传字段。"""
        ...


if __name__ == '__main__':
    pass
