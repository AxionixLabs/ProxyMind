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


def is_transport_close_exception(exc: BaseException) -> bool:
    """判断异常组是否只包含 MCP 关闭期可忽略的传输断开异常。"""
    transport_close_types = {
        "httpx.ReadError",
        "httpx.WriteError",
        "httpx.CloseError",
        "httpcore.ReadError",
        "httpcore.WriteError",
        "httpcore.CloseError",
        "anyio.EndOfStream",
        "anyio.BrokenResourceError",
        "anyio.ClosedResourceError"
    }
    items = list(flatten_exceptions(exc))
    if not items:
        return False

    return all(exception_type_name(item) in transport_close_types for item in items)


if __name__ == '__main__':
    pass
