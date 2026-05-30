# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

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
    """返回跨平台归一化后的命令 basename，供策略判断使用。"""
    text = str(value or "").strip().replace("\\", "/")
    return text.rsplit("/", 1)[-1].lower()


def is_python_head(value: typing.Any) -> bool:
    return command_head(value) in PYTHON_HEADS


if __name__ == '__main__':
    pass
