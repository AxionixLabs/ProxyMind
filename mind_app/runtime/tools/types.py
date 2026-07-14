# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing


class ToolDisplayResult(typing.Protocol):
    """描述工具结果展示层需要读取的字段。"""
    ok: bool
    fields: typing.Union[str, dict[str, typing.Any]]
    text: str
    data: typing.Any
    cost_ms: int


if __name__ == '__main__':
    pass
