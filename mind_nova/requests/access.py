# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

AccessMode = typing.Literal["safe", "full"]

DEFAULT_ACCESS_MODE: AccessMode = "safe"
ACCESS_MODE_SET: set[str]       = {"safe", "full"}


def normalize_access_mode(value: typing.Any) -> AccessMode:
    """把外部输入归一化为服务端接受的 access mode。"""
    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in {"full", "full_access"}:
        return "full"
    return DEFAULT_ACCESS_MODE


def access_mode_label(value: typing.Any) -> str:
    """返回标题栏和命令输出使用的权限文案。"""
    return "Elevated" if normalize_access_mode(value) == "full" else "Approval"


def apply_access_mode(
    payload: dict[str, typing.Any],
    value: typing.Any = DEFAULT_ACCESS_MODE
) -> dict[str, typing.Any]:
    """向远端请求载荷写入 access mode。"""
    payload["access_mode"] = normalize_access_mode(value)
    return payload


if __name__ == '__main__':
    pass
