# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from pydantic import Field

ProxyHostArg = typing.Annotated[
    typing.Optional[str],
    Field(description="设备可访问的 Helix 主机地址；为空时自动推断。"),
]
CaptureIdArg = typing.Annotated[
    str,
    Field(description="capture_start 返回的抓包会话标识。"),
]
HostArg = typing.Annotated[
    typing.Optional[str],
    Field(description="按主机名精确筛选。"),
]
PathArg = typing.Annotated[
    typing.Optional[str],
    Field(description="按路径片段筛选。"),
]
MethodArg = typing.Annotated[
    typing.Optional[str],
    Field(description="按 HTTP 方法筛选。"),
]
StatusCodeArg = typing.Annotated[
    typing.Optional[int],
    Field(description="按 HTTP 状态码精确筛选。"),
]
LimitArg = typing.Annotated[
    int,
    Field(description="最多返回的流记录数，范围为 1 到 500。"),
]
ExportFormatArg = typing.Annotated[
    typing.Literal["har", "json"],
    Field(description="导出格式：har 或 json。"),
]
RuleArg = typing.Annotated[
    dict[str, typing.Any],
    Field(description="断言对象：host、path、method、status_code、min_count、max_count、max_duration_ms。"),
]


if __name__ == '__main__':
    pass
