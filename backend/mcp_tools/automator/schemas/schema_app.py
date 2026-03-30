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
ApkPathArg = typing.Annotated[
    str,
    Field(description="本地 APK 文件路径。")
]
ReplaceArg = typing.Annotated[
    bool,
    Field(description="安装时若应用已存在，是否允许覆盖安装。")
]
DowngradeArg = typing.Annotated[
    bool,
    Field(description="安装时是否允许版本降级。")
]
TestOnlyArg = typing.Annotated[
    bool,
    Field(description="是否按 test APK 方式安装。")
]
KeepDataArg = typing.Annotated[
    bool,
    Field(description="卸载应用时是否保留应用数据目录。")
]


if __name__ == '__main__':
    pass
