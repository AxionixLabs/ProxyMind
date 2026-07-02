# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from pydantic import Field

LogKeywordsArg = typing.Annotated[
    typing.Optional[list[str]],
    Field(description="大小写不敏感的 OR 关键词列表；为空时不过滤关键词。"),
]
LogTagsArg = typing.Annotated[
    typing.Optional[list[str]],
    Field(description="logcat tag 过滤列表；为空时不过滤 tag。"),
]
LogLevelArg = typing.Annotated[
    str,
    Field(description="logcat 最低级别过滤，如 `V`、`D`、`I`、`W`、`E`。"),
]
MaxLinesArg = typing.Annotated[
    int,
    Field(description="最多保留的日志行数。"),
]
SavedPathArg = typing.Annotated[
    typing.Optional[str],
    Field(description="完整日志落盘路径；为空时只返回摘要。"),
]


if __name__ == '__main__':
    pass
