# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing


class _ApplicationBaseError(BaseException):
    """作为可预期入口错误的基础类型。"""


class AppError(_ApplicationBaseError):
    """描述可直接展示给调用方的应用错误。"""

    def __init__(self, message: typing.Any) -> None:
        self.message = message

    def __str__(self) -> str:
        return f"<AppError> {self.message}"

    __repr__ = __str__


__all__ = ("AppError",)

