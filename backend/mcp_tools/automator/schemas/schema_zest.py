# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from pydantic import Field

RefreshTtlArg = typing.Annotated[
    float,
    Field(description="设备列表缓存复用窗口，单位秒。")
]


if __name__ == '__main__':
    pass
