# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from pydantic import Field

ScreenshotLocalArg = typing.Annotated[
    str,
    Field(
        min_length=1,
        pattern=r"\S",
        description="截图保存目录或图片基准路径。",
    )
]
PackageKeywordArg = typing.Annotated[
    typing.Optional[str],
    Field(description="包名过滤关键字；为空时按 `scope` 返回完整候选列表。")
]
PackageScopeArg = typing.Annotated[
    typing.Literal["user", "system", "all"],
    Field(description="包名查询范围，可选用户应用、系统应用或全部应用。")
]


if __name__ == '__main__':
    pass
