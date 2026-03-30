# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from pydantic import Field


FreeRuleMessageArg = typing.Annotated[
    str,
    Field(description="要交给上层规则链处理的自然语言请求。")
]
FreeRuleContextArg = typing.Annotated[
    typing.Optional[dict[str, typing.Any]],
    Field(description="补充上下文信息字典，供上层规则链参考。")
]


if __name__ == '__main__':
    pass
