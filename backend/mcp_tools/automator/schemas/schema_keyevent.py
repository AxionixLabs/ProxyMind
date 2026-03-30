# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from pydantic import Field


LongPressArg = typing.Annotated[
    bool,
    Field(description="是否以长按方式发送该按键事件。")
]


if __name__ == '__main__':
    pass
