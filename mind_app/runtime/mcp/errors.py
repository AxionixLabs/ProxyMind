# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing


def flatten_exceptions(exc: BaseException) -> typing.Iterator[BaseException]:
    """展开异常组，便于按底层异常做判断。"""
    if isinstance(exc, BaseExceptionGroup):
        for item in exc.exceptions:
            yield from flatten_exceptions(item)
        return

    yield exc


def exception_type_name(exc: BaseException) -> str:
    """返回异常的模块限定名，用于识别可选依赖异常。"""
    return f"{type(exc).__module__}.{type(exc).__name__}"


def summarize_exception(exc: BaseException) -> str:
    """从异常组中挑一个可读异常摘要用于 debug 日志。"""
    for item in flatten_exceptions(exc):
        text = str(item).strip()
        if text:
            return f"{type(item).__name__}: {text}"

    return f"{type(exc).__name__}: {exc}"


if __name__ == '__main__':
    pass
