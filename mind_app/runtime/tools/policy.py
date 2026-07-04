# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing


def supports_parallel(
    name: str,
    meta: dict[str, typing.Any] | None = None
) -> bool:
    """判断工具是否允许并行执行。"""
    _ = name
    if not isinstance(meta, dict):
        return False
    if bool(meta.get("hidden", False)):
        return False
    return bool(
        meta.get("supports_parallel") is True
        or meta.get("supports_parallel_tool_calls") is True
        or meta.get("parallel") is True
    )


if __name__ == '__main__':
    pass
