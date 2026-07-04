# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

PYTHON_HEADS = frozenset({
    "python",
    "python.exe",
    "python3",
    "python3.exe",
    "py",
    "py.exe"
})


def command_head(value: typing.Any) -> str:
    """返回归一化后的命令名称。"""
    text = str(value or "").strip().replace("\\", "/")
    return text.rsplit("/", 1)[-1].lower()


def is_python_head(value: typing.Any) -> bool:
    """判断命令名称是否为 Python 启动器。"""
    return command_head(value) in PYTHON_HEADS


if __name__ == '__main__':
    pass

