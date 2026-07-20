# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing


class _MindBaseError(BaseException):
    """作为 Mind 可预期入口错误的基础类型。"""


class MindError(_MindBaseError):
    """描述可直接展示给调用方的 Mind 错误。"""

    def __init__(self, message: typing.Any) -> None:
        self.message = message

    def __str__(self) -> str:
        return f"<MindError> {self.message}"

    __repr__ = __str__


if __name__ == '__main__':
    pass
