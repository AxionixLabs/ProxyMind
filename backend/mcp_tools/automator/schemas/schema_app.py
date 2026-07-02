# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from pydantic import Field

UrlArg = typing.Annotated[
    str,
    Field(description="要发送给系统处理的 deep link URL。")
]
ActivityArg = typing.Annotated[
    typing.Optional[str],
    Field(description="目标 Activity；为空时使用应用默认入口。")
]


if __name__ == '__main__':
    pass
