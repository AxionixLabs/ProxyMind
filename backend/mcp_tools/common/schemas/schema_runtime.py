# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from pydantic import Field

DelayArg = typing.Annotated[
    float,
    Field(description="固定等待的秒数，支持小数秒。")
]


if __name__ == '__main__':
    pass
